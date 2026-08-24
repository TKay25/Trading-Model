"""Server-side AutoTrader.

Moves the auto-trade OPEN and CLOSE logic (previously in the browser) into a
background daemon thread so the bot keeps trading even when the dashboard page
is closed. It uses the SAME reversal engine as the frontend (TDI + Bollinger/
RSI bounce + M/W/H&S + candlestick confluence) and the persistent shared Deriv
connection, so it behaves exactly like the old in-browser auto-trader:

  - OPEN  : a BUY/SELL reversal on 5m/15m/30m (1m-confirmed strength >= min)
            opens a MULTIPLIER trade with NO fixed SL/TP — the position runs
            until the reversal exit closes it, the user closes it manually, or
            Deriv auto-closes it at -100% of the stake (multiplier behaviour).
            MULTIPLE positions per symbol are allowed (same direction); the bot
            never opens the opposite direction of an existing position (no
            self-hedging) and caps open positions per symbol.
  - CLOSE : a trade-strength reversal OPPOSITE to a held direction closes those
            positions (e.g. a BUY reversal closes the running SELL positions on
            that instrument, then the same signal can open a BUY).

Config is pushed from the dashboard (POST /api/auto/config) and persisted to a
small JSON file so the choices survive restarts.
"""
import asyncio
import json
import logging
import os
import threading
import time

from trading_service import Candle, PatternRecognizer
from reversal_engine import ReversalEngine
from deriv_api import SharedDerivConnection

logger = logging.getLogger(__name__)

_CONF_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auto_trader_config.json")
_SIGNAL_TFS = ("5m", "15m", "30m")
_CONFIRM_TF = "1m"
_COOLDOWN = 90.0          # seconds between auto-actions per (symbol, timeframe)
_INTERVAL = 25            # seconds between scan/decision cycles
_SCAN_COUNT = 150         # candles per timeframe per symbol
_MAX_POSITIONS_PER_SYMBOL = 5   # safety cap on open positions per symbol


class AutoTrader:
    """Background thread that opens/closes multiplier trades on reversals."""

    def __init__(self, resolve_multiplier, record_trade, position_monitor,
                 symbols, timeframes, app_id, account_type, token):
        # resolve_multiplier(symbol, requested) -> nearest valid multiplier
        self._resolve_multiplier = resolve_multiplier
        # record_trade(contract_id, symbol, contract_type, lot, mult, sl, tp)
        self._record_trade = record_trade
        self._pm = position_monitor
        self._symbols = list(symbols)
        self._tfs = dict(timeframes)
        self._engine = ReversalEngine()
        # DEDICATED connection: the AutoTrader's heavy scan (40 candle fetches
        # every cycle) must NOT share the frontend's connection, or frontend
        # requests (/api/candles, /api/connect, /api/multipliers) get starved
        # and time out on Render while the bot keeps trading.
        self._token = token
        self._conn = SharedDerivConnection(app_id=app_id, account_type=account_type)
        self._conn.start()

        self._lock = threading.Lock()
        self._thread = None
        self._running = False

        # config (persisted so it survives restarts / works with the page closed).
        # Default is AUTO-TRADE ON: after a restart the bot keeps trading with no
        # browser open. The dashboard toggles can still turn it off (that choice
        # is persisted too).
        self._cfg = {
            "enabled": True,
            "min_strength": 30,
            "paper": False,
            "exit_on_reversal": True,
            "stake": 1.0,
            "multiplier": 100,
        }
        self._cooldown = {}        # (symbol, tf) -> last OPEN ts
        self._exit_cooldown = {}   # (symbol, direction) -> last close ts
        self._last_cycle = {"at": None, "opened": [], "closed": [], "scan": 0,
                            "message": "not started"}
        self._load_cfg()

    # ------------------------------------------------------------- config
    def update_config(self, **kw):
        with self._lock:
            for k in ("enabled", "min_strength", "paper", "exit_on_reversal",
                      "stake", "multiplier"):
                if k in kw:
                    self._cfg[k] = kw[k]
            cfg = dict(self._cfg)
        self._save_cfg()
        return cfg

    def get_config(self):
        with self._lock:
            return dict(self._cfg)

    def get_status(self):
        with self._lock:
            return {"config": dict(self._cfg), **dict(self._last_cycle)}

    def _save_cfg(self):
        try:
            with open(_CONF_PATH, "w") as f:
                json.dump(self._cfg, f)
        except Exception:
            pass

    def _load_cfg(self):
        try:
            with open(_CONF_PATH) as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k in self._cfg:
                    if k in data:
                        self._cfg[k] = data[k]
        except Exception:
            pass

    # ----------------------------------------------------------- lifecycle
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="auto-trader")
        self._thread.start()
        logger.info("Auto-trader started (enabled=%s exit_on_reversal=%s)",
                    self._cfg.get("enabled"), self._cfg.get("exit_on_reversal"))

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)

    def _run(self):
        while self._running:
            try:
                self._cycle()
            except Exception:
                logger.exception("Auto-trader cycle crashed")
            # sleep in small slices so stop() can interrupt promptly
            for _ in range(20):
                if not self._running:
                    break
                time.sleep(_INTERVAL / 20)

    # --------------------------------------------------------------- cycle
    def _cycle(self):
        with self._lock:
            cfg = dict(self._cfg)
        if not (cfg.get("enabled") or cfg.get("exit_on_reversal")):
            self._set_last(at=time.time(), opened=[], closed=[], scan=0,
                           message="auto-trade off")
            return

        # 1) candles for all symbols x [1m,5m,15m,30m] over ONE shared connection
        candles_by = self._scan_candles()
        if not candles_by:
            self._set_last(at=time.time(), message="scan returned nothing")
            return

        # 2) reversal + 1m-confirmed strength per symbol on 5m/15m/30m
        signals = {}
        for sym, by_tf in candles_by.items():
            confirm = self._norm(by_tf.get(_CONFIRM_TF))
            for tf in _SIGNAL_TFS:
                sc = self._norm(by_tf.get(tf))
                if not sc or len(sc) < 50:
                    continue
                try:
                    tdi = self._engine.tdi(sc)
                    pats = PatternRecognizer.detect_reversal_patterns(sc)
                    rev = self._engine.reversal(tdi, pats, sc)
                    if rev in ("BUY", "SELL"):
                        s = int(round((self._engine.strength(rev, tdi, sc, confirm) or 0) * 100))
                        signals.setdefault(sym, []).append({"tf": tf, "rev": rev, "str": s})
                except Exception:
                    continue

        # 3) open positions per symbol (direction)
        open_syms = self._open_symbols()

        opened, closed = [], []
        now = time.time()

        # ---- CLOSE: a trade-strength reversal OPPOSITE to a held direction
        # closes those positions (e.g. a BUY reversal closes the running SELL
        # positions on that instrument). Only the opposing direction is closed,
        # and only when the reversal is at/above the open threshold (>= min) —
        # a weak opposite signal does NOT close positions. ----
        closed_dirs = {}   # sym -> {direction: closed count this cycle}
        if cfg.get("exit_on_reversal"):
            min_str = cfg.get("min_strength", 30)
            for sym, counts in open_syms.items():
                for d in counts:                      # each held direction
                    opposite_rev = "SELL" if d == "BUY" else "BUY"
                    if not any(h["rev"] == opposite_rev and h["str"] >= min_str
                               for h in signals.get(sym, [])):
                        continue
                    ckey = (sym, d)
                    if now - self._exit_cooldown.get(ckey, 0) < _COOLDOWN:
                        continue
                    self._exit_cooldown[ckey] = now
                    try:
                        n = self._close_symbol(sym, d)
                        if n:
                            closed.append(f"{sym}:{d}x{n}")
                            closed_dirs.setdefault(sym, {})[d] = \
                                closed_dirs.get(sym, {}).get(d, 0) + n
                    except Exception as e:
                        logger.warning("Auto-exit failed for %s: %r", sym, e)

        # ---- OPEN: strong reversal signals. Multiple positions per symbol are
        # allowed (one per timeframe per cooldown window), but the bot never
        # opens the OPPOSITE direction of a position it already holds, and it
        # caps the number of open positions per symbol as a safety limit. ----
        if cfg.get("enabled"):
            cycle_opened = {}   # sym -> directions opened THIS cycle
            for sym, hits in signals.items():
                counts = open_syms.get(sym) or {}
                if sum(counts.values()) >= _MAX_POSITIONS_PER_SYMBOL:
                    continue
                for hit in hits:
                    opposite = "SELL" if hit["rev"] == "BUY" else "BUY"
                    opened_dirs = cycle_opened.get(sym, set())
                    # no self-hedging: skip if we still hold the opposite direction
                    # (positions closed this cycle don't count as still held)
                    held_opposite = counts.get(opposite, 0) - \
                        closed_dirs.get(sym, {}).get(opposite, 0)
                    if held_opposite > 0 or opposite in opened_dirs:
                        continue
                    ckey = (sym, hit["tf"])
                    if now - self._cooldown.get(ckey, 0) < _COOLDOWN:
                        continue
                    if hit["str"] < cfg.get("min_strength", 30):
                        continue
                    self._cooldown[ckey] = now
                    try:
                        if self._open_trade(sym, hit, cfg):
                            opened.append(f"{sym}:{hit['rev']}@{hit['str']}% ({hit['tf']})")
                            cycle_opened.setdefault(sym, set()).add(hit["rev"])
                    except Exception as e:
                        logger.warning("Auto-open failed for %s: %r", sym, e)

        self._set_last(at=now, opened=opened, closed=closed,
                       scan=sum(len(v) for v in candles_by.values()),
                       message="idle" if not (opened or closed)
                       else f"opened {len(opened)}, closed {len(closed)}")
        if opened or closed:
            logger.info("AutoTrader cycle: opened=%s closed=%s", opened, closed)

    # ------------------------------------------------------------- helpers
    def _set_last(self, **kw):
        with self._lock:
            self._last_cycle.update(kw)

    def _call(self, coro_factory, authenticated=False, timeout=45):
        """Run a coroutine over THIS AutoTrader's DEDICATED connection so its
        heavy scanning never starves the frontend's shared connection."""
        try:
            return self._conn.call(coro_factory, token=self._token, timeout=timeout)
        except (asyncio.TimeoutError, TimeoutError):
            raise TimeoutError(f"Deriv request timed out after {timeout}s") from None

    @staticmethod
    def _norm(candles):
        """Raw get_candles dicts -> list of trading_service.Candle."""
        out = []
        for c in candles or []:
            try:
                out.append(Candle(
                    timestamp=int(c.get("epoch") or c.get("timestamp") or 0),
                    open=float(c["open"]),
                    high=float(c["high"]),
                    low=float(c["low"]),
                    close=float(c["close"]),
                ))
            except (TypeError, KeyError, ValueError):
                continue
        return out

    def _scan_candles(self):
        async def _scan(api):
            keys = [(s, tf) for s in self._symbols
                    for tf in (_CONFIRM_TF,) + _SIGNAL_TFS]
            coros = [api.get_candles(s, self._tfs[tf], _SCAN_COUNT) for (s, tf) in keys]
            outs = await asyncio.gather(*coros, return_exceptions=True)
            data = {}
            for (s, tf), res in zip(keys, outs):
                if isinstance(res, dict) and "candles" in res:
                    data.setdefault(s, {})[tf] = res["candles"]
            return data
        try:
            return self._call(_scan, authenticated=False, timeout=45)
        except Exception as e:
            logger.warning("Auto-trader scan failed: %r", e)
            return {}

    def _open_symbols(self):
        """Return {symbol: {direction: count}} of currently open positions."""
        out = {}
        try:
            async def _port(api):
                return await api.get_portfolio()
            port = self._call(_port, authenticated=True, timeout=30)
            pt = port.get("portfolio", {}) if isinstance(port, dict) else {}
            contracts = pt.get("contracts", []) if isinstance(pt, dict) else []
            for c in contracts:
                sym = c.get("underlying_symbol")
                ct = str(c.get("contract_type") or "").upper()
                if not sym:
                    continue
                direction = "SELL" if any(x in ct for x in ("DOWN", "PUT")) else "BUY"
                counts = out.setdefault(sym, {})
                counts[direction] = counts.get(direction, 0) + 1
        except Exception as e:
            logger.warning("Auto-trader portfolio fetch failed: %r", e)
        return out

    def _close_symbol(self, symbol, direction):
        """Sell every open position on `symbol` that is in `direction` (BUY/SELL)."""
        async def _close(api):
            port = await api.get_portfolio()
            pt = port.get("portfolio", {}) if isinstance(port, dict) else {}
            contracts = pt.get("contracts", []) if isinstance(pt, dict) else []
            closed = []
            for c in contracts:
                cid = c.get("contract_id")
                ct = str(c.get("contract_type") or "").upper()
                if not cid or (c.get("underlying_symbol") or "") != symbol:
                    continue
                cdir = "SELL" if any(x in ct for x in ("DOWN", "PUT")) else "BUY"
                if cdir != direction:
                    continue
                try:
                    await api.sell_contract(cid)
                    closed.append(cid)
                except Exception:
                    continue
            return closed
        cids = self._call(_close, authenticated=True, timeout=45)
        for cid in cids:
            self._pm.untrack(cid)
        if cids:
            logger.info("Auto-trader closed %d %s position(s) on %s", len(cids), direction, symbol)
        return len(cids)

    def _open_trade(self, symbol, hit, cfg):
        stake = float(cfg.get("stake", 1.0) or 1.0)
        direction = hit["rev"]
        requested = int(cfg.get("multiplier", 100) or 100)
        multiplier = self._resolve_multiplier(symbol, requested)
        contract_type = "MULTUP" if direction == "BUY" else "MULTDOWN"

        # USER RULE: NO fixed SL/TP amounts. The position stays open until the
        # reversal exit fires (opposite TDI + M/W + candlestick signal), the
        # user closes it manually, or Deriv auto-closes it at -100% of the stake.
        sl = tp = 0.0

        if cfg.get("paper"):
            logger.info("PAPER auto-trade %s %s @ %s%% (%s) lot=%.3f mult=%s (no SL/TP — reversal exit only)",
                        direction, symbol, hit["str"], hit["tf"], stake, multiplier)
            return True

        async def _buy(api):
            return await api.buy_multiplier(symbol, stake, direction, multiplier)

        result = self._call(_buy, authenticated=True, timeout=45)
        if isinstance(result, dict) and "buy" in result:
            cid = result["buy"]["contract_id"]
            if sl > 0 or tp > 0:
                self._pm.track(cid, symbol, sl, tp)
            self._record_trade(cid, symbol, contract_type, stake, multiplier, sl, tp)
            logger.info("Auto-trade opened %s %s @ %s%% lot=%.3f mult=%s -> cid=%s (no SL/TP — reversal exit only)",
                        direction, symbol, hit["str"], stake, multiplier, cid)
            return True
        logger.warning("Auto-trade rejected for %s: %s", symbol, result)
        return False
