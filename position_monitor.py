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
MISS_LIMIT = 8                # consecutive empty polls before we drop a contract

# ---------------------------------------------------------------------------
# LIVE P/L CACHE + THROTTLING (2026-09-21)
#
# Deriv rate-limits `proposal_open_contract` per ACCOUNT, and hard: measured
# with the bot fully STOPPED on a single fresh session, 1 of 14 back-to-back
# calls was accepted, and 0 of 14 after an 8-second quiet period -- so this is
# not a burst limit that refills in a second.
#
# We were blowing through it because FOUR components each fired one call per
# open contract:
#     * this monitor                 (every position, every 2s)
#     * app._reconcile_open_results  (semaphore 6)
#     * /api/positions               (semaphore 10, on EVERY UI poll)
#     * AutoTrader adoption sweep
# With ~19 positions that is 40-60 calls per cycle. The failure was brutal and
# silent: every call after the first came back `{"error":{"code":"RateLimit"}}`,
# this monitor only inspects `profit`, so it saw NO data for ANY contract,
# concluded the LINK was dead and reconnected -- forever (observed: a reconnect
# every 10s, 19 positions with ZERO stop-loss enforcement). Each reconnect
# opened ANOTHER session, which made the account-wide throttling worse: a
# self-amplifying loop.
#
# So: this monitor is the ONLY component allowed to call
# proposal_open_contract. Everyone else reads LIVE below (thread-safe), and a
# RateLimit is treated as "slow down" -- never as a dead link or a dead
# contract.
# ---------------------------------------------------------------------------
POLL_GAP = 0.30               # min seconds between POC calls inside one sweep
RATE_LIMIT_MIN_BACKOFF = 20.0
RATE_LIMIT_MAX_BACKOFF = 300.0

LIVE = {}                     # cid(int) -> {profit, status, ts, stop, take_profit, symbol}
_live_lock = threading.Lock()
_live_health = {"last_ok": 0.0, "rate_limited": 0, "throttled": False, "backoff": 0.0}


class RateLimited(Exception):
    """Deriv throttled proposal_open_contract.

    Deliberately NOT a ConnectionError: the link is fine and the contracts are
    fine. Tearing the connection down (the old behaviour) leaked a session per
    attempt and made the account-wide throttling worse.
    """
    def __init__(self, delay, reason="RateLimit"):
        super().__init__(reason)
        self.delay = delay
        self.reason = reason

_paths_lock = threading.Lock()
trade_paths = {}              # contract_id (int) -> record
_last_save = 0.0


def live_snapshot(cids=None):
    """Thread-safe copy of the last known live P/L per contract.

    The UI and any other reader should use THIS instead of calling
    proposal_open_contract themselves -- one poller, one shared quota.
    """
    with _live_lock:
        if cids is None:
            return dict(LIVE)
        out = {}
        for c in cids:
            try:
                k = int(c)
            except (TypeError, ValueError):
                continue
            if k in LIVE:
                out[k] = LIVE[k]
        return out


def live_health():
    """Diagnostics: is the P/L feed healthy, or are we being throttled?"""
    with _live_lock:
        h = dict(_live_health)
        h["cached"] = len(LIVE)
    h["throttled"] = bool(h["throttled"] and
                          (time.time() - (h["last_ok"] or 0)) > 30)
    return h


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
            # Contracts that stopped answering and were dropped: normally ones
            # that closed while the process was down. Non-zero here means we
            # restored a stale record — worth investigating, not a normal exit.
            "vanished_exits": sum(1 for r in closed if r.get("exit_reason") == "vanished"),
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


# Human-readable exit attribution for the history / analysis tables. The raw
# `exit_reason` answers "what ended this position?", which is exactly what you
# want next to a loss: was it a stop-loss, a reversal exit, Deriv's own -100%
# close, or an artefact of the bot restarting?
_EXIT_LABELS = {
    "stop_loss": "Stop-loss",
    "take_profit": "Take-profit",
    "reversal_exit": "Reversal",
    "profit_target": "Profit target",
    "close_all": "Close all",
    "manual": "Manual",
    "settled": "Settled",
    "deriv_auto_close": "-100% auto-close",
    "closed_while_down": "Closed while down",
    "vanished": "Dropped (no data)",
}

# A stop that fired within this many seconds of ADOPTION was almost certainly
# installed already-breached by the adoption sweep rather than triggered by the
# market: an adopted record's t_open is the ADOPTION moment, so hold_s ~ 0 means
# "sold seconds after we started watching it". Measured 2026-09-21: 19 of 53
# recorded stop-outs were this (median hold 14s vs 202s for real stops). They are
# policy artefacts, NOT strategy outcomes, so they are flagged here explicitly.
ADOPT_ARTIFACT_S = 120


def exit_reason_map():
    """contract_id -> human label, for the trade history and analysis tables."""
    out = {}
    with _paths_lock:
        recs = list(trade_paths.items())
    for cid, r in recs:
        if not isinstance(r, dict):
            continue
        reason = r.get("exit_reason")
        if not reason:
            out[cid] = "Tracking" if not r.get("t_close") else "Closed"
            continue
        label = _EXIT_LABELS.get(reason, reason)
        if (reason == "stop_loss" and r.get("adopted")
                and (r.get("hold_s") or 0) < ADOPT_ARTIFACT_S):
            label += " (adopted at boot)"
        out[cid] = label
    return out


load_trade_paths()


class PositionMonitor:
    """Tracks SL/TP levels and auto-closes positions when they are hit."""

    def __init__(self):
        self._limits = {}          # contract_id -> {symbol, stop_loss, take_profit, ...}
        self._lock = threading.Lock()
        self._thread = None
        self._running = False
        self._reconciled = False   # startup restore/reconcile done exactly once

    # ---- lifecycle ----------------------------------------------------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        # BEFORE the loop starts: re-register every position that was open when
        # the process stopped. Tracking lives only in memory, so without this a
        # restart leaves positions with no stop until the adoption sweep runs.
        self.restore_open()
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
                "_misses": 0,          # consecutive empty polls (see _check)
                "rec": rec,
            }
        # Publish the record to the dataset IMMEDIATELY, not on the next
        # _persist_open sweep (every ~15 cycles, which can be minutes away with
        # dozens of positions). Until it is in `trade_paths` the position exists
        # ONLY in memory, so a crash/restart loses it entirely — the next process
        # then cannot restore it and the AutoTrader's adoption sweep installs a
        # FRESH ATR stop that may already be breached, liquidating the position
        # on arrival (observed 2026-09-21: ~11 positions sold at -0.11..-0.92
        # within seconds of adopting, stops of -0.12).
        with _paths_lock:
            trade_paths[cid] = rec
        save_trade_paths()         # throttled (<=1 write / 30s)

    def is_tracked(self, contract_id):
        with self._lock:
            return int(contract_id) in self._limits

    def limits_for(self, contract_id):
        """The enforced geometry for one contract, or None when untracked.

        Lets the AutoTrader MIRROR the monitor's stop/target onto the contract
        itself via Deriv's `contract_update`, so protection survives this process
        dying. Returns the ORIGINAL -stop_loss / +take_profit deliberately:
        `stop` is the RATCHETED level (break-even / trailing), a moving target
        that only the monitor can follow as profit grows. Copying that onto the
        contract would freeze the position at a level it has already passed.
        """
        with self._lock:
            lim = self._limits.get(int(contract_id))
            if not lim:
                return None
            rec = lim.get("rec") or {}
            return {"symbol": lim.get("symbol") or "",
                    "stop_loss": float(lim.get("stop_loss") or 0),
                    "take_profit": float(lim.get("take_profit") or 0),
                    "stake": float(rec.get("stake") or 0)}

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

    def _register(self, cid, rec):
        """Register ONE persisted record for SL/TP enforcement.

        Returns False for a record with no geometry (a manual/observational trade
        has nothing to enforce) — never invent a stop that was never set.
        """
        try:
            sl = float(rec.get("stop_loss") or 0)
            tp = float(rec.get("take_profit") or 0)
        except (TypeError, ValueError):
            return False
        if not sl and not tp:
            return False
        live = rec.pop("_live", None) or {}
        with self._lock:
            self._limits[cid] = {
                "symbol": rec.get("symbol") or "",
                "stop_loss": sl,
                "take_profit": tp,
                # Reinstate the DYNAMIC stop (break-even/trailing may have moved
                # it) rather than resetting to -stop_loss, which would silently
                # give back profit the position had already locked in.
                "stop": float(live.get("stop", -sl)),
                "break_even": bool(live.get("be")),
                "trail": bool(live.get("trail")),
                "break_even_pct": float(live.get("be_pct") or 0.5),
                "trail_pct": float(live.get("trail_pct") or 0.5),
                "max_profit": float(live.get("max_profit") or 0.0),
                "_misses": 0,
                "restored": True,
                "rec": rec,
            }
        return True

    @staticmethod
    def _persisted_open():
        """Open (not-yet-closed) records from the persisted path dataset."""
        with _paths_lock:
            return {cid: dict(r) for cid, r in trade_paths.items()
                    if isinstance(r, dict) and not r.get("t_close")}

    def restore_open(self, only=None):
        """Re-register positions that were still open when the process stopped.

        Tracking lives ONLY in memory (`self._limits`), so a restart used to
        leave every open position with NO stop and NO target until the
        AutoTrader's adoption sweep ran (up to 300s later) — and that sweep
        REFUSES any position whose `multiplier x 1.5 x ATR` exceeds its
        timeframe risk cap, leaving it with no stop at all and only Deriv's
        -100% close to end it. That is the measured cause of trades losing far
        more than their stop-loss.

        `trade_paths.json` already persists every open position (symbol, stop,
        target, direction, timeframe, entry metadata), so we can reinstate
        EXACTLY the geometry the position was opened with: no ATR re-estimation,
        so neither the adoption bug (a 1m ATR applied to a 30m position) nor the
        affordability test can silently drop a live position.

        `only` restricts registration to those contract ids (the live portfolio).
        The original record is REUSED, so MAE/MFE sampling continues across the
        restart instead of starting a fresh path.
        """
        restored = 0
        for cid, rec in self._persisted_open().items():
            if only is not None and cid not in only:
                continue
            if self.is_tracked(cid):
                continue
            if self._register(cid, rec):
                restored += 1
        if restored:
            logger.info("Position monitor restored %d open position(s) from %s "
                        "with their original SL/TP", restored,
                        os.path.basename(PATHS_FILE))
        return restored

    def drop_missing(self, live_ids):
        """Retire persisted records whose contract is NO LONGER open.

        Such a record is a PHANTOM: the position settled while the process was
        down. Re-registering it would install a stop on a contract that no longer
        exists and inflate the "unprotected position" count. Found in testing —
        a batch of stake-relative-era records (sl=0.20/tp=5.00) were still marked
        open long after their contracts had gone, which is exactly the geometry
        measured to be broken (17/17 stop-outs).

        REFUSES to act on an EMPTY live set: an empty portfolio is how Deriv
        answers an over-quota or otherwise degraded authenticated request, so
        `drop_missing(set())` would retire EVERY record while the positions are
        still open at Deriv (see _restore_live).
        """
        if not live_ids:
            logger.warning("drop_missing called with an EMPTY live set — refusing to "
                           "retire any record (an empty portfolio means UNKNOWN, "
                           "not 'everything closed')")
            return 0
        dropped = 0
        for cid, rec in self._persisted_open().items():
            if cid in live_ids:
                continue
            if self.is_tracked(cid):
                self.untrack(cid, reason="closed_while_down")
            else:
                self._flush(cid, {"rec": rec}, "closed_while_down")
            dropped += 1
        if dropped:
            logger.info("Position monitor retired %d stale record(s) that settled "
                        "while the bot was down", dropped)
        return dropped

    async def _restore_live(self, api):
        """Startup reconciliation: protect ONLY what is genuinely still open."""
        try:
            port = await api.get_portfolio()
            pt = port.get("portfolio", {}) if isinstance(port, dict) else {}
            contracts = pt.get("contracts", []) if isinstance(pt, dict) else []
            live = {int(c["contract_id"]) for c in contracts if c.get("contract_id")}
        except Exception as e:
            logger.warning("Restore: portfolio fetch failed (%r) — restoring every "
                           "persisted open record WITHOUT verification", e)
            self.restore_open()
            return
        if not live:
            # *** AN EMPTY PORTFOLIO IS NOT EVIDENCE THAT NOTHING IS OPEN ***
            # Deriv answers an OVER-QUOTA authenticated request with an empty
            # payload rather than an error (measured 2026-09-21), so "empty" and
            # "you have no positions" are indistinguishable. Acting on it is
            # catastrophic: `drop_missing(set())` retired EVERY persisted record
            # while the positions were still open, leaving 19 real positions with
            # no stop, no target and no tracking — and the dashboard reported a
            # flat book, so nothing looked wrong. Treat the open set as UNKNOWN
            # and restore unverified (same as the fetch-failed path above).
            logger.warning("Restore: portfolio came back EMPTY — treating the open "
                           "set as UNKNOWN and restoring every persisted open record "
                           "unverified (this is how Deriv answers an over-quota call)")
            self.restore_open()
            return
        self.drop_missing(live)
        n = self.restore_open(only=live)
        logger.info("Position monitor reconcile: %d position(s) re-protected, "
                    "account reports %d open", n, len(live))

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
        rec.pop("_live", None)        # internal live-state snapshot, not dataset
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
            recs = {}
            for cid, lim in self._limits.items():
                r = lim.get("rec")
                if not r:
                    continue
                r = dict(r)
                # The reward/stop ENFORCEMENT state (not just the opening
                # geometry) must survive too, or a restored position would come
                # back with a stale stop level. Underscore = internal; stripped
                # again in _flush so the dataset stays clean.
                r["_live"] = {"stop": lim.get("stop"),
                               "max_profit": lim.get("max_profit"),
                               "be": lim.get("break_even"),
                               "trail": lim.get("trail"),
                               "be_pct": lim.get("break_even_pct"),
                               "trail_pct": lim.get("trail_pct")}
                recs[cid] = r
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
                if not self._reconciled:
                    # Nothing tracked YET — but persisted records may still need
                    # reconciling, and this branch is where the loop would
                    # otherwise sleep forever and never restore them.
                    if api is None:
                        api = DerivAPI(app_id=Config.DERIV_APP_ID,
                                       api_token=Config.DERIV_API_TOKEN,
                                       account_type=Config.DERIV_ACCOUNT_TYPE)
                        await api.connect(authenticated=True)
                    self._reconciled = True
                    await self._restore_live(api)
                    continue          # re-read _limits (now populated)
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
                if not self._reconciled:
                    self._reconciled = True
                    await self._restore_live(api)
                    continue          # re-read _limits before polling

            try:
                await self._check(api, tracked)
            except RateLimited as e:
                # THROTTLED, not broken. Keep the session: reconnecting would
                # leak another session and worsen the account-wide throttling
                # (that loop is exactly what disabled SL/TP enforcement once
                # every proposal_open_contract call started returning RateLimit).
                cycles += 1
                if cycles % 15 == 0:
                    self._persist_open()
                await asyncio.sleep(e.delay)
                continue
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
        answered = 0        # contracts that returned usable data this pass
        pending_miss = []   # contracts with no data, judged once we know the link is alive
        rate_limited = 0    # contracts Deriv refused because the ACCOUNT is throttled
        for cid, lim in tracked.items():
            if not self._running:
                return
            # ISOLATE EACH CONTRACT. This loop previously let any exception
            # escape; a ConnectionError re-raised to _loop, which reconnects and
            # RESTARTS THE PASS FROM THE FIRST CONTRACT — so with ~30 positions
            # one slow/dead contract could starve every contract after it, cycle
            # after cycle (observed as records with samples=0 after 49 minutes of
            # being tracked). A failure on one position must never blind the rest.
            try:
                resp = await api._send_request(
                    {"proposal_open_contract": 1, "contract_id": int(cid)}
                )
            except ConnectionError:
                raise
            except Exception as e:
                logger.debug("Position monitor poll failed for %s: %r", cid, e)
                resp = None

            # Space the calls out. Firing one per contract as fast as the socket
            # allows is what tripped Deriv's rate limit in the first place.
            await asyncio.sleep(POLL_GAP)

            err = ((resp or {}).get("error") or {}).get("code")
            if err:
                if err == "RateLimit":
                    # The ACCOUNT is throttled. The remaining contracts will be
                    # throttled too, so stop the sweep and surrender the rest of
                    # this pass rather than burning the quota on rejections.
                    # NOT a vanished contract and NOT a dead link.
                    rate_limited += 1
                    logger.debug("Position monitor throttled on %s", cid)
                    break
                logger.debug("Position monitor poll error for %s: %s", cid, err)
                continue

            poc = (resp or {}).get("proposal_open_contract") or {}
            status = poc.get("status")
            profit = poc.get("profit")

            if status in ("sold", "expired", "won", "lost"):
                self._sample(lim, profit)
                self._publish_live(cid, lim, profit, status)
                self.untrack(cid, reason="settled")
                continue
            if profit is None:
                # No data for this contract. Defer the decision: a broken LINK
                # also produces empty responses for EVERY contract, and we must
                # not mistake that for 30 dead contracts.
                pending_miss.append(cid)
                continue
            answered += 1
            lim["_misses"] = 0

            profit = float(profit)
            self._sample(lim, profit)
            self._publish_live(cid, lim, profit, status)

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

        # ---- link-vs-contract disambiguation (2026-09-21) -------------------
        # If NOTHING answered this pass, the LINK is broken, not the contracts.
        # Counting misses here would drop every tracked position at once and leave
        # the account unprotected — observed: 16 positions dropped inside 5
        # seconds right after a restart, while every Deriv call returned empty
        # (a broken connection is indistinguishable from "all contracts gone"
        # ---- throttling is neither a dead link nor a dead contract ---------
        # Checked BEFORE the link-vs-contract rule below, because a RateLimit
        # produces exactly the same symptom (no `profit` for any contract) and
        # used to be misread as a dead link -- which reconnected, which opened a
        # new session, which made the account-wide throttling worse. That loop
        # ran every 10s with 19 unprotected positions behind it.
        if rate_limited:
            delay = self._note_rate_limited()
            if answered == 0:
                raise RateLimited(delay, f"RateLimit x{rate_limited}")
            logger.info("Position monitor partially throttled (%d refused, %d "
                        "answered) - continuing without reconnecting",
                        rate_limited, answered)

        # unless you look at the pass as a whole). Raising forces _loop to close
        # and re-establish the connection instead of discarding our protection.
        if pending_miss and answered == 0 and len(tracked) > 1:
            logger.warning("Position monitor: no contract answered this pass "
                           "(%d tracked) — treating as a CONNECTION problem, not as "
                           "vanished contracts; reconnecting", len(tracked))
            raise ConnectionError("no contract answered")
        # At least one contract answered, so the link IS alive; only now is it
        # safe to believe that these specific contracts no longer exist.
        for cid in pending_miss:
            lim = tracked.get(cid)
            if lim is None:
                continue
            miss = int(lim.get("_misses") or 0) + 1
            lim["_misses"] = miss
            if miss >= MISS_LIMIT:
                self.untrack(cid, reason="vanished")
                logger.warning("Position monitor dropped %s after %d empty "
                               "response(s) — contract gone while the bot was down?",
                               cid, miss)

    # ------------------------------------------------------------------
    # Live P/L publishing + throttling bookkeeping
    # ------------------------------------------------------------------
    def _publish_live(self, cid, lim, profit, status):
        """Record the latest known P/L so the UI/AutoTrader can read it cached.

        This is what makes the monitor the ONLY proposal_open_contract caller:
        everyone else reads this instead of spending the account's rate limit.
        It also carries the EFFECTIVE stop (the one break-even/trailing move),
        so the dashboard can show a stop that has ratcheted into profit.
        """
        now = time.time()
        with _live_lock:
            LIVE[int(cid)] = {
                "profit": None if profit is None else float(profit),
                "status": status,
                "ts": now,
                "stop": lim.get("stop"),
                "take_profit": lim.get("take_profit"),
                "stop_loss": lim.get("stop_loss"),
                "symbol": lim.get("symbol"),
                "break_even": lim.get("break_even"),
                "trail": lim.get("trail"),
            }
            _live_health["last_ok"] = now
            _live_health["rate_limited"] = 0
            _live_health["throttled"] = False
            _live_health["backoff"] = 0.0
            # Prune long-dead entries so this cannot grow forever, but keep them
            # long enough for the results reconciler to read the final status of
            # a contract that just settled.
            if len(LIVE) > 400:
                for k in [k for k, v in LIVE.items()
                          if (v.get("status") or "open") != "open"
                          and (now - (v.get("ts") or 0)) > 900]:
                    LIVE.pop(k, None)

    def _note_rate_limited(self):
        """Deriv throttled us: back off exponentially and RECORD it.

        We deliberately keep the connection. Closing it would open a new session
        on the next attempt, and the account is throttled as a whole -- so
        reconnecting adds load and leaks sessions rather than helping.
        """
        with _live_lock:
            _live_health["rate_limited"] += 1
            _live_health["throttled"] = True
            n = _live_health["rate_limited"]
            delay = min(RATE_LIMIT_MIN_BACKOFF * (2 ** max(0, n - 1)),
                        RATE_LIMIT_MAX_BACKOFF)
            _live_health["backoff"] = delay
        logger.warning("Position monitor THROTTLED by Deriv (rate limit #%d on "
                       "proposal_open_contract) - backing off %.0fs WITHOUT "
                       "reconnecting, so open positions stay tracked", n, delay)
        return delay

    async def _close(self, api):
        try:
            await api.close()
        except Exception:
            pass
