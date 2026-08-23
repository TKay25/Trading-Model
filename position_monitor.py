"""SL/TP auto-close monitor for binary trades (background thread).

Binary CALL/PUT contracts don't support native stop-loss/take-profit on Deriv,
so this monitor tracks open positions that were opened with profit/loss targets
and auto-sells the contract once the take-profit or stop-loss level is reached
(it reads the current P/L via `proposal_open_contract`).
"""
import asyncio
import logging
import threading

from config import Config
from deriv_api import DerivAPI

logger = logging.getLogger(__name__)


class PositionMonitor:
    """Tracks SL/TP levels and auto-closes positions when they are hit."""

    def __init__(self):
        self._limits = {}          # contract_id -> {symbol, stop_loss, take_profit, ...}
        self._lock = threading.Lock()
        self._thread = None
        self._running = False

    # ---- lifecycle ----------------------------------------------------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="position-monitor")
        self._thread.start()
        logger.info("Position monitor started")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)

    # ---- tracking -------------------------------------------------------
    def track(self, contract_id, symbol="", stop_loss=0, take_profit=0,
              break_even=False, trail=False, break_even_pct=0.5, trail_pct=0.5):
        with self._lock:
            self._limits[int(contract_id)] = {
                "symbol": symbol or "",
                "stop_loss": float(stop_loss or 0),
                "take_profit": float(take_profit or 0),
                # Dynamic profit level at/below which we sell. Starts at -stop_loss;
                # break-even moves it to 0; trailing raises it to lock in profit.
                "stop": -float(stop_loss or 0),
                "break_even": bool(break_even),
                "trail": bool(trail),
                "break_even_pct": float(break_even_pct),
                "trail_pct": float(trail_pct),
                "max_profit": 0.0,
            }

    def untrack(self, contract_id):
        with self._lock:
            self._limits.pop(int(contract_id), None)

    def untrack_all(self):
        with self._lock:
            self._limits.clear()

    def tracked_count(self):
        with self._lock:
            return len(self._limits)

    # ---- background loop -------------------------------------------------
    def _run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._loop())
        except Exception:
            logger.exception("Position monitor loop crashed")
        finally:
            loop.close()
            logger.info("Position monitor stopped")

    async def _loop(self):
        api = None
        while self._running:
            with self._lock:
                tracked = dict(self._limits)

            if not tracked:
                if api is not None:
                    await self._close(api)
                    api = None
                await asyncio.sleep(1)
                continue

            if api is None:
                api = DerivAPI(app_id=Config.DERIV_APP_ID,
                               api_token=Config.DERIV_API_TOKEN,
                               account_type=Config.DERIV_ACCOUNT_TYPE)
                await api.connect(authenticated=True)
                logger.info("Position monitor connected (authenticated)")

            try:
                await self._check(api, tracked)
            except Exception as e:
                logger.warning(f"Position monitor check failed (%r); reconnecting", e)
                await self._close(api)
                api = None

            await asyncio.sleep(2)

        if api is not None:
            await self._close(api)

    async def _check(self, api, tracked):
        for cid, lim in tracked.items():
            if not self._running:
                return
            try:
                resp = await api._send_request(
                    {"proposal_open_contract": 1, "contract_id": int(cid)}
                )
            except ConnectionError:
                raise

            poc = resp.get("proposal_open_contract") or {}
            status = poc.get("status")
            profit = poc.get("profit")

            if status in ("sold", "expired", "won", "lost"):
                self.untrack(cid)
                continue
            if profit is None:
                continue

            profit = float(profit)
            tp = lim.get("take_profit") or 0
            stop = lim.get("stop", -lim.get("stop_loss", 0))

            # --- Take profit ---
            if tp and profit >= tp:
                await api._send_request({"sell": int(cid), "price": 0})
                self.untrack(cid)
                logger.info("Take-profit hit for %s: profit=%.2f (target %.2f)",
                            cid, profit, tp)
                continue

            # --- Break-even: once profit reaches a fraction of TP, move SL to 0 ---
            if lim.get("break_even") and tp and profit >= tp * lim.get("break_even_pct", 0.5) and stop < 0:
                stop = 0.0
                lim["stop"] = 0.0
                logger.info("Break-even reached for %s: profit=%.2f (SL moved to 0)", cid, profit)

            # --- Trailing: keep the stop a fraction of TP below the best profit ---
            # Only activates once the position has actually been in profit
            # (max_profit > 0), and never moves the stop below the original
            # stop-loss level — otherwise a fresh position with trail on would
            # be stopped out at $0 profit the moment it ticks slightly red.
            if lim.get("trail") and tp:
                if profit > lim["max_profit"]:
                    lim["max_profit"] = profit
                if lim["max_profit"] > 0:
                    base = -float(lim.get("stop_loss") or 0)
                    candidate = max(base, lim["max_profit"] - tp * lim.get("trail_pct", 0.5))
                    if candidate > stop:
                        stop = candidate
                        lim["stop"] = candidate

            # --- Stop loss (original or dynamic) ---
            # Active if the user set an SL, OR a dynamic stop has been raised above 0
            # (which only happens once break-even/trailing have locked in some profit).
            if (lim.get("stop_loss") or stop > 0) and profit <= stop:
                await api._send_request({"sell": int(cid), "price": 0})
                self.untrack(cid)
                logger.info("Stop-loss hit for %s: profit=%.2f (stop %.2f)",
                            cid, profit, stop)

    async def _close(self, api):
        try:
            await api.close()
        except Exception:
            pass
