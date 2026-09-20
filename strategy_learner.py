"""Continuous strategy fitter ("the learner") for BotTraderX5.

WHAT IT HONES (and what it deliberately never touches)
------------------------------------------------------
The signal engine (TDI + M/W/H&S + candlesticks reversal confluence) is FIXED —
the learner does not invent entries. It continuously re-estimates, from the
bot's own live results:

  * the exit geometry per (symbol, timeframe): k_sl / k_tp in ATR units
  * which (symbol, timeframe) pairs show a measurable edge at all, and which
    should be pruned
  * the implied risk per trade, so an unaffordable geometry is never proposed

It cannot change leverage, the risk ceiling, the stake, symbol/timeframe
blacklists or the on/off switch. Those stay human rules.

HOW IT LEARNS WITHOUT GUESSING
------------------------------
Every tracked position is polled ~every 2s and its excursion envelope is
recorded to trade_paths.json: profit_min (MAE), profit_max (MFE), and WHEN each
extreme happened (t_min / t_max). That makes counterfactual exits EXACT rather
than simulated: to score a candidate stop/target we only need to know which
level price reached FIRST, and the recorded ordering tells us.

  dip to -0.30 then rally to +2.00, with a candidate stop at -0.20:
      t_min < t_max  ->  the stop would have closed the trade  ->  -0.20
Even though the trade really ended +2.00, the counterfactual is -0.20 — which is
exactly the effect we are trying to measure. No market re-sampling needed.

SAFETY RAILS
------------
  * Only recorded excursions are used; never projections.
  * A group needs >= MIN_TRADES closed records before it is fitted at all.
  * Out-of-sample split (older 70% fit / newest 30% check) must ALSO be
    positive, so parameters are not fitted to the tail of one regime.
  * Proposed parameters must beat the current global defaults on the fit window.
  * `adopted` records and records from before the ATR-geometry build are
    excluded (their entry geometry differs).
  * Everything is written to strategy_params.json with the evidence behind each
    decision, so any change can be audited or reverted.
"""
import json
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
PATHS_FILE = os.path.join(_HERE, "trade_paths.json")
RESULTS_FILE = os.path.join(_HERE, "trade_results.json")
PARAMS_FILE = os.path.join(_HERE, "strategy_params.json")

REFIT_EVERY = 900.0        # seconds between refits (15 min)
FIRST_FIT_DELAY = 90.0     # let the bot collect a little data after boot
MIN_TRADES = 30            # closed records needed before a group may be fitted
MIN_OOS_TRADES = 6         # closed records needed in the out-of-sample slice
OOS_FRACTION = 0.30        # newest share held out for validation
MIN_EV_PCT = 0.0           # fitted mean % of stake per trade must exceed this
BEAT_MARGIN = 0.02         # must beat the current globals by this many %-of-stake
MAX_PATHS_READ = 20000

# k_sl / k_tp grid, in ATR units. k_tp must exceed k_sl.
K_SL_GRID = (1.0, 1.5, 2.0, 3.0, 4.0, 6.0)
K_TP_GRID = (1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0)


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------
def load_records():
    """Closed trade-path records usable for fitting.

    A record is usable when it has the entry geometry we need to re-score it:
    atr_pct (% of price), multiplier, stake, and the excursion extremes. Records
    flagged `adopted` are dropped — we did not choose their entry, and their
    timeframe is often unknown.
    """
    try:
        with open(PATHS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        logger.warning("learner: cannot read %s (%r)", PATHS_FILE, e)
        return []
    out = []
    for rec in list(raw.values())[:MAX_PATHS_READ]:
        if not isinstance(rec, dict) or not rec.get("t_close"):
            continue
        if rec.get("adopted"):
            continue
        if rec.get("stop_move_pct") is None:      # pre-ATR build: different geometry
            continue
        if not rec.get("atr_pct") or not rec.get("multiplier") or not rec.get("stake"):
            continue
        if rec.get("profit_min") is None or rec.get("profit_max") is None:
            continue
        out.append(rec)
    out.sort(key=lambda r: r.get("t_close") or 0)
    return out


# ---------------------------------------------------------------------------
# counterfactual exit scoring
# ---------------------------------------------------------------------------
def simulate(rec, k_sl, k_tp, multiplier=None):
    """P/L as a FRACTION OF STAKE for a candidate (k_sl, k_tp) exit rule.

    Uses the recorded excursion envelope and the ORDER of its extremes. Returns
    None when the trade cannot be scored.
    """
    try:
        atr = float(rec["atr_pct"])
        mult = float(multiplier if multiplier is not None else rec["multiplier"])
        stake = float(rec["stake"])
    except (KeyError, TypeError, ValueError):
        return None
    if atr <= 0 or mult <= 0 or stake <= 0 or k_sl <= 0 or k_tp <= 0:
        return None

    stop_move = k_sl * atr                          # % of price
    tp_move = stop_move * (k_tp / k_sl)
    stop_usd = min(stake, stake * mult * stop_move / 100.0)
    tp_usd = stake * mult * tp_move / 100.0
    if stop_usd <= 0 or tp_usd <= 0:
        return None

    mae = float(rec.get("profit_min") or 0.0)       # worst excursion, in $
    mfe = float(rec.get("profit_max") or 0.0)       # best excursion, in $
    final = float(rec.get("profit_last") or 0.0)
    hit_sl = mae <= -stop_usd
    hit_tp = mfe >= tp_usd

    if hit_sl and hit_tp:
        t_min, t_max = rec.get("t_min"), rec.get("t_max")
        if t_min is not None and t_max is not None and t_min != t_max:
            pnl = tp_usd if t_max < t_min else -stop_usd
        else:
            pnl = final          # order unknown: fall back to what really happened
    elif hit_sl:
        pnl = -stop_usd
    elif hit_tp:
        pnl = tp_usd
    else:
        pnl = final              # neither level breached: the real exit ruled
    return pnl / stake


def _risk_pct(k_sl, atr_pct, multiplier):
    """Stop distance as a fraction of the stake at a given multiplier."""
    try:
        return float(multiplier) * float(k_sl) * float(atr_pct) / 100.0
    except (TypeError, ValueError):
        return None


def _score(records, k_sl, k_tp, multiplier):
    vals = []
    for rec in records:
        v = simulate(rec, k_sl, k_tp, multiplier)
        if v is not None:
            vals.append(v)
    if not vals:
        return None
    wins = sum(1 for v in vals if v >= 0)
    return {
        "n": len(vals),
        "ev_pct": sum(vals) / len(vals),
        "total_pct": sum(vals),
        "win_rate": wins / len(vals),
    }


def _reference_multiplier(records):
    """The multiplier the group's trades actually used (most common)."""
    counts = {}
    for rec in records:
        m = rec.get("multiplier")
        if m:
            counts[int(m)] = counts.get(int(m), 0) + 1
    if not counts:
        return None
    return max(counts, key=lambda k: counts[k])


# ---------------------------------------------------------------------------
# fitting
# ---------------------------------------------------------------------------
def _risk_cap(cfg, timeframe, default=0.80):
    """Per-timeframe risk cap (fraction of stake), mirroring
    AutoTrader._risk_cap so the learner never proposes a geometry that live
    trading would refuse. 0 for a timeframe means "not traded"."""
    try:
        per_tf = cfg.get("risk_by_tf") or {}
        if timeframe and timeframe in per_tf:
            cap = float(per_tf[timeframe])
        else:
            cap = float(cfg.get("max_risk_pct") or default)
    except (TypeError, ValueError):
        cap = default
    return min(max(cap, 0.0), 0.98)


def fit_group(records, cfg):
    """Fit (k_sl, k_tp) for one (symbol, timeframe) group.

    Returns a decision dict. A group is only promoted when it has enough data,
    the fitted rule is out-of-sample positive, and it beats the current global
    defaults. Groups with enough data and no edge are proposed for pruning.
    """
    cfg = cfg or {}
    max_risk = _risk_cap(cfg, records[0].get("timeframe") if records else None)
    cur_sl = float(cfg.get("sl_atr_k") or 1.5)
    cur_tp = float(cfg.get("tp_atr_k") or 6.0)
    mult = _reference_multiplier(records)
    avg_atr = sum(float(r["atr_pct"]) for r in records) / len(records)

    decision = {
        "n": len(records),
        "multiplier": mult,
        "avg_atr_pct": round(avg_atr, 4),
        "current": {"sl_atr_k": cur_sl, "tp_atr_k": cur_tp},
    }
    if len(records) < MIN_TRADES:
        decision.update(status="insufficient_data",
                        needed=MIN_TRADES,
                        ev_pct=None)
        return decision

    # walk-forward split: fit on the older part, validate on the newest part
    split = max(1, int(len(records) * (1.0 - OOS_FRACTION)))
    fit_recs, oos_recs = records[:split], records[split:]

    best = None
    for k_sl in K_SL_GRID:
        risk = _risk_pct(k_sl, avg_atr, mult)
        if risk is None or risk > max_risk:
            continue                      # would not be takeable live
        for k_tp in K_TP_GRID:
            if k_tp <= k_sl:
                continue
            sc = _score(fit_recs, k_sl, k_tp, mult)
            if not sc or sc["n"] < max(5, MIN_TRADES // 3):
                continue
            if best is None or sc["ev_pct"] > best[0]["ev_pct"]:
                best = (sc, k_sl, k_tp, risk)

    base = _score(fit_recs, cur_sl, cur_tp, mult)
    decision["baseline_ev_pct"] = round(base["ev_pct"], 4) if base else None

    if best is None:
        decision.update(status="unaffordable",
                        note="every grid stop costs more than max_risk_pct")
        return decision

    sc, k_sl, k_tp, risk = best
    oos = _score(oos_recs, k_sl, k_tp, mult) if len(oos_recs) >= MIN_OOS_TRADES else None
    decision.update(
        proposed={"sl_atr_k": k_sl, "tp_atr_k": k_tp},
        fit_ev_pct=round(sc["ev_pct"], 4),
        fit_n=sc["n"],
        fit_win_rate=round(sc["win_rate"], 3),
        risk_pct=round(risk, 4),
        oos_ev_pct=(round(oos["ev_pct"], 4) if oos else None),
        oos_n=(oos["n"] if oos else 0),
    )

    # --- promotion gates -------------------------------------------------
    if sc["ev_pct"] <= MIN_EV_PCT:
        decision.update(status="prune",
                        note="best fitted rule is not positive — stop trading this market")
        return decision
    if oos is None:
        decision.update(status="insufficient_oos",
                        note=f"needs {MIN_OOS_TRADES} out-of-sample trades")
        return decision
    if oos["ev_pct"] <= MIN_EV_PCT:
        decision.update(status="hold_oos_negative",
                        note="out-of-sample slice is not positive — not promoting")
        return decision
    if base and abs(k_sl - cur_sl) < 1e-9 and abs(k_tp - cur_tp) < 1e-9:
        decision.update(status="hold_is_optimal", note="current globals already win")
        return decision
    if base and sc["ev_pct"] - base["ev_pct"] < BEAT_MARGIN:
        decision.update(status="hold_marginal",
                        note="improvement is within noise — keeping the globals")
        return decision

    decision.update(status="promote",
                    note="out-of-sample positive and beats the globals")
    return decision


def fit_all(records=None, cfg=None):
    """Fit every (symbol, timeframe) group present in the data."""
    records = load_records() if records is None else records
    groups = {}
    for rec in records:
        key = f"{rec.get('symbol')}|{rec.get('timeframe')}"
        groups.setdefault(key, []).append(rec)

    decisions = {}
    for key, recs in sorted(groups.items()):
        decisions[key] = fit_group(recs, cfg)
    promoted = {k: v for k, v in decisions.items() if v.get("status") == "promote"}
    pruned = {k: v for k, v in decisions.items() if v.get("status") == "prune"}
    return {
        "generated_at": time.time(),
        "records": len(records),
        "groups": decisions,
        "params": {k: v["proposed"] for k, v in promoted.items()},
        "skip": sorted(pruned.keys()),
        "thresholds": {"min_trades": MIN_TRADES, "min_oos_trades": MIN_OOS_TRADES,
                       "oos_fraction": OOS_FRACTION, "beat_margin": BEAT_MARGIN},
    }


# ---------------------------------------------------------------------------
# background runner
# ---------------------------------------------------------------------------
class StrategyLearner:
    """Refits the exit geometry on a timer and publishes strategy_params.json."""

    def __init__(self, cfg_fn=None, enabled=True):
        self._cfg_fn = cfg_fn              # () -> auto-trader config dict
        self._lock = threading.Lock()
        self._params = {}                  # "symbol|tf" -> {sl_atr_k, tp_atr_k}
        self._skip = set()
        self._state = {"runs": 0, "last_run": None, "records": 0,
                       "promoted": 0, "pruned": 0, "message": "not started"}
        self._last_result = None
        self._thread = None
        self._running = False
        self._enabled = bool(enabled)
        self._load_params()

    # ---- persistence ----
    def _load_params(self):
        try:
            with open(PARAMS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._params = dict(data.get("params") or {})
                self._skip = set(data.get("skip") or [])
                if self._params or self._skip:
                    logger.info("learner: loaded %d fitted group(s), %d pruned",
                                len(self._params), len(self._skip))
        except Exception:
            pass

    def _save(self, result):
        try:
            tmp = PARAMS_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=1)
            os.replace(tmp, PARAMS_FILE)
        except Exception as e:
            logger.warning("learner: could not save params (%r)", e)

    # ---- lookups used by the AutoTrader ----
    def params_for(self, symbol, timeframe):
        """Fitted parameters for a market, or None to use the global defaults."""
        with self._lock:
            return self._params.get(f"{symbol}|{timeframe}")

    def is_pruned(self, symbol, timeframe):
        with self._lock:
            return f"{symbol}|{timeframe}" in self._skip

    def get_applied(self):
        """What the bot is actually using right now (vs what the last fit said)."""
        with self._lock:
            return {"groups": {k: dict(v) for k, v in self._params.items()},
                    "pruned": sorted(self._skip)}

    def get_state(self):
        with self._lock:
            return dict(self._state)

    def get_last_fit(self):
        """The most recent fit result (in memory, else read back from disk).

        NOTE: deliberately a method, not `learner.PARAMS_FILE` — module-level
        constants are NOT instance attributes, so that would silently fail and
        an API would serve a fallback instead of real data (the 2026-09-20 bug).
        """
        with self._lock:
            if self._last_result is not None:
                return self._last_result
        try:
            with open(PARAMS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            with self._lock:
                self._last_result = data
            return data
        except Exception:
            return {"groups": {}, "params": {}, "skip": [], "records": 0,
                    "thresholds": {"min_trades": MIN_TRADES,
                                   "min_oos_trades": MIN_OOS_TRADES,
                                   "oos_fraction": OOS_FRACTION,
                                   "beat_margin": BEAT_MARGIN},
                    "note": "no fit has been published yet"}

    # ---- lifecycle ----
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="strategy-learner")
        self._thread.start()
        logger.info("Strategy learner started (refit every %.0fs, min %d trades/group)",
                    REFIT_EVERY, MIN_TRADES)

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)

    def _run(self):
        time.sleep(FIRST_FIT_DELAY)
        while self._running:
            try:
                self.run_once()
            except Exception:
                logger.exception("learner refit failed")
            for _ in range(int(REFIT_EVERY / 5)):
                if not self._running:
                    break
                time.sleep(5)

    def run_once(self):
        """One refit cycle. Safe to call directly (used by tests + the API)."""
        cfg = {}
        if self._cfg_fn:
            try:
                cfg = self._cfg_fn() or {}
            except Exception:
                cfg = {}
        if not self._enabled:
            with self._lock:
                self._state["message"] = "learning disabled"
            return {"success": True, "enabled": False}

        result = fit_all(cfg=cfg)
        result["enabled"] = True
        with self._lock:
            self._params = dict(result["params"])
            self._skip = set(result["skip"])
            self._state = {
                "runs": self._state.get("runs", 0) + 1,
                "last_run": result["generated_at"],
                "records": result["records"],
                "promoted": len(result["params"]),
                "pruned": len(result["skip"]),
                "message": (f"{result['records']} usable records · "
                            f"{len(result['params'])} fitted group(s) · "
                            f"{len(result['skip'])} pruned"),
            }
        self._save(result)
        with self._lock:
            self._last_result = result
        if result["params"] or result["skip"]:
            logger.info("learner: %d fitted, %d pruned (from %d records)",
                        len(result["params"]), len(result["skip"]), result["records"])
        return result
