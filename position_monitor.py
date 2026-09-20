"""SL/TP auto-close monitor for binary trades (background thread).

Binary CALL/PUT contracts don't support native stop-loss/take-profit on Deriv,
so this monitor tracks open positions that were opened with profit/loss targets
and auto-sells the contract once the take-profit or stop-loss level is reached
(it reads the current P/L via `proposal_open_contract`).
"""
import asyncio
import json
import logging
import os
import threading
import time

from config import Config
from deriv_api import DerivAPI

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TRADE-PATH LOGGING (MAE / MFE) — the training data for adaptive exits.
#
# Why: P/L here is dominated by EXIT quality, but Deriv only ever tells us a
# closed contract's FINAL profit. Without the path we cannot answer the one
# question that matters — "would a tighter stop have killed a trade that went
# on to recover?" So while a position is open (we already poll its P/L every
# ~2s) we record the excursion envelope: min/max profit reached, when, and how
# many samples. That turns stop/target tuning from a guess into a measurement.
#
# Record per contract (flushed when the position closes):
#   cid, symbol, direction, timeframe, signal_strength, stake, multiplier,
#   stop_loss, take_profit, t_open, t_close, hold_s, samples,
#   profit_min/max/last (+ *_pct = as a fraction of stake), t_min/t_max
#   (seconds from open), exit_reason, adopted
# ---------------------------------------------------------------------------
PATHS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trade_paths.json")
MAX_PATHS = 20000
_SAVE_EVERY = 30.0            # seconds between throttled disk writes

_paths_lock = threading.Lock()
trade_paths = {}              # contract_id (int) -> record
_last_save = 0.0


def load_trade_paths():
    """Load previously recorded trade paths (called on import)."""
    global trade_paths
    if not os.path.exists(PATHS_FILE):
        return
    try:
        with open(PATHS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            trade_paths = {int(k): v for k, v in data.items()
                           if str(k).lstrip("-").isdigit()}
        logger.info("Loaded %d trade path record(s)", len(trade_paths))
    except Exception as e:
        logger.warning("Failed to load trade paths: %r", e)


def save_trade_paths(force=False):
    """Persist atomically (tmp + os.replace) so a crash can never truncate it."""
    global _last_save
    now = time.time()
    if not force and (now - _last_save) < _SAVE_EVERY:
        return
    _last_save = now
    try:
        with _paths_lock:
            snapshot = dict(trade_paths)
        tmp = PATHS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snapshot, f)
        os.replace(tmp, PATHS_FILE)
    except Exception as e:
        logger.warning("Failed to save trade paths: %r", e)


def path_summary():
    """Aggregate view of recorded paths — answers "does a tight stop pay?"."""
    with _paths_lock:
        recs = list(trade_paths.values())
    closed = [r for r in recs if r.get("t_close")]
    out = {"total": len(recs), "closed": len(closed), "open": len(recs) - len(closed)}
    if closed:
        def avg(key):
            vals = [r.get(key) for r in closed if r.get(key) is not None]
            return round(sum(vals) / len(vals), 4) if vals else None
        out.update({
            "stop_loss_exits": sum(1 for r in closed if r.get("exit_reason") == "stop_loss"),
            "take_profit_exits": sum(1 for r in closed if r.get("exit_reason") == "take_profit"),
            "settled_exits": sum(1 for r in closed if r.get("exit_reason") in ("settled", "deriv_auto_close")),
            # THE key number: trades that went deep red and still ended green —
            # i.e. winners a tighter stop would have destroyed.
            "dipped_then_recovered": sum(
                1 for r in closed
                if (r.get("profit_min_pct") or 0) < -0.15 and (r.get("profit_final_pct") or 0) >= 0),
            "avg_mae_pct": avg("profit_min_pct"),
            "avg_mfe_pct": avg("profit_max_pct"),
            "avg_hold_s": avg("hold_s"),
        })
    return out


load_trade_paths()


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
              break_even=False, trail=False, break_even_pct=0.5, trail_pct=0.5,
              meta=None):
        """Start tracking a position.

        `meta` is free-form entry context (stake, multiplier, direction,
        timeframe, signal_strength, ...) copied into the trade-path record so
        the dataset is self-contained (no join against the ledger needed).
        """
        cid = int(contract_id)
        rec = {
            "cid": cid,
            "symbol": symbol or "",
            "direction": None,
            "timeframe": None,
            "signal_strength": None,
            "stake": None,
            "multiplier": None,
            "adopted": False,
            # exit geometry (ATR mode) — kept so stops can be re-fitted per
            # (symbol, timeframe) from this dataset alone
            "atr_pct": None,
            "stop_move_pct": None,
            "risk_pct": None,
            "t_open": time.time(),
            "t_close": None,
            "hold_s": None,
            "samples": 0,
            "profit_first": None,
            "profit_min": None,
            "profit_max": None,
            "profit_last": None,
            "t_min": None,
            "t_max": None,
            "exit_reason": None,
        }
        for k, v in (meta or {}).items():
            if k in rec:
                rec[k] = v
        rec["stop_loss"] = float(stop_loss or 0)
        rec["take_profit"] = float(take_profit or 0)
        with self._lock:
            self._limits[cid] = {
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
                "rec": rec,
            }

    def is_tracked(self, contract_id):
        with self._lock:
            return int(contract_id) in self._limits

    def tracked_ids(self):
        with self._lock:
            return list(self._limits)

    def untrack(self, contract_id, reason=None):
        """Stop tracking a position and flush its trade-path record.

        This is the SINGLE choke point for every exit path (stop-loss,
        take-profit, Deriv settlement, manual sell, reversal exit, close-all),
        so every closed trade lands in the path dataset exactly once.
        """
        cid = int(contract_id)
        with self._lock:
            lim = self._limits.pop(cid, None)
        if lim:
            self._flush(cid, lim, reason)

    def untrack_all(self, reason="close_all"):
        with self._lock:
            items = list(self._limits.items())
            self._limits.clear()
        for cid, lim in items:
            self._flush(cid, lim, reason)

    def tracked_count(self):
        with self._lock:
            return len(self._limits)

    # ---- trade-path recording -------------------------------------------
    @staticmethod
    def _sample(lim, profit):
        """Record one excursion sample (MAE/MFE) for the tracked position."""
        rec = lim.get("rec")
        if rec is None or profit is None:
            return
        try:
            p = float(profit)
        except (TypeError, ValueError):
            return
        now = time.time()
        opened = rec.get("t_open") or now
        rec["samples"] = (rec.get("samples") or 0) + 1
        rec["profit_last"] = p
        if rec.get("profit_first") is None:
            rec["profit_first"] = p
        if rec.get("profit_min") is None or p < rec["profit_min"]:
            rec["profit_min"] = p
            rec["t_min"] = round(now - opened, 1)
        if rec.get("profit_max") is None or p > rec["profit_max"]:
            rec["profit_max"] = p
            rec["t_max"] = round(now - opened, 1)

    def _flush(self, cid, lim, reason=None):
        """Finalise + store a position's path record (called on every exit)."""
        rec = lim.get("rec")
        if not rec:
            return
        rec["t_close"] = time.time()
        rec["hold_s"] = round(rec["t_close"] - (rec.get("t_open") or rec["t_close"]), 1)
        if reason:
            rec["exit_reason"] = reason
        elif not rec.get("exit_reason"):
            rec["exit_reason"] = "closed"
        stake = rec.get("stake")

        def pct(v):
            try:
                return round(float(v) / float(stake), 4) if stake else None
            except (TypeError, ValueError):
                return None

        rec["profit_min_pct"] = pct(rec.get("profit_min"))
        rec["profit_max_pct"] = pct(rec.get("profit_max"))
        rec["profit_final_pct"] = pct(rec.get("profit_last"))
        with _paths_lock:
            trade_paths[cid] = rec
            if len(trade_paths) > MAX_PATHS:
                excess = len(trade_paths) - MAX_PATHS
                for _cid in sorted(trade_paths,
                                   key=lambda c: trade_paths[c].get("t_open") or 0)[:excess]:
                    trade_paths.pop(_cid, None)
        save_trade_paths()

    def _persist_open(self):
        """Snapshot LIVE records so in-flight positions survive a crash/restart.
        A completed record (t_close set) is never overwritten."""
        with self._lock:
            recs = {cid: dict(lim["rec"])
                    for cid, lim in self._limits.items() if lim.get("rec")}
        if not recs:
            return
        with _paths_lock:
            for cid, r in recs.items():
                cur = trade_paths.get(cid)
                if cur and cur.get("t_close"):
                    continue
                trade_paths[cid] = r
        save_trade_paths()

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
        cycles = 0
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

            # Snapshot in-flight paths every ~30s so a crash/restart keeps the
            # partial excursion data of positions that are still open.
            cycles += 1
            if cycles % 15 == 0:
                self._persist_open()

            await asyncio.sleep(2)

        # Shutdown: keep the excursion data we already gathered.
        self._persist_open()
        save_trade_paths(force=True)
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
                self._sample(lim, profit)
                self.untrack(cid, reason="settled")
                continue
            if profit is None:
                continue

            profit = float(profit)
            self._sample(lim, profit)

            if status is None:
                # Deriv auto-closes a multiplier at -100% of the stake and then
                # reports that contract with status ABSENT (open contracts always
                # carry status="open"). Record it and stop tracking.
                self.untrack(cid, reason="deriv_auto_close")
                continue

            tp = lim.get("take_profit") or 0
            stop = lim.get("stop", -lim.get("stop_loss", 0))

            # --- Take profit ---
            if tp and profit >= tp:
                await api._send_request({"sell": int(cid), "price": 0})
                self.untrack(cid, reason="take_profit")
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
                self.untrack(cid, reason="stop_loss")
                logger.info("Stop-loss hit for %s: profit=%.2f (stop %.2f)",
                            cid, profit, stop)

    async def _close(self, api):
        try:
            await api.close()
        except Exception:
            pass
