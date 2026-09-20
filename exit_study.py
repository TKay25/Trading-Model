"""Exit-rule study: compare candidate stop/target rules on the SAME signals.

WHY THIS EXISTS
---------------
The user asked whether the stop-loss should simply equal the lot size (i.e. risk
the whole stake). The live path data CANNOT answer that, because the logger stops
recording when a position closes: every trade that hit its stop is CENSORED at
that moment. A trade that dipped to -20%, got stopped and would then have rallied
to +50% is recorded as MFE ~ 0 and a loss. So "0 winners out of 26, best-ever
favourable excursion +6% of stake" is equally consistent with:

    (a) the entries have no edge, and
    (b) the stop is too tight and truncates recoveries.

Only a study that walks the FULL price path can separate them, which is what this
does: the same engine, the same signals, several exit rules, real high/low path,
no spread, no execution, no censoring.

METHOD
------
For each (symbol, timeframe) and each bar i:
  1. run the live signal engine on candles[:i+1]
  2. if BUY/SELL, evaluate EVERY rule below on the identical entry (close[i])
  3. walk forward up to MAX_BARS bars, stop checked FIRST inside a bar (conservative)
  4. record P/L as a % OF STAKE = price_move% x multiplier (the live relationship:
     exposure = stake x multiplier, so a 1% move at 40x = 40% of the stake)

The multiplier is held at the symbol's LOWEST valid value for every rule, so the
comparison isolates the EXIT RULE rather than leverage.
"""
import json
import os
import time
import urllib.request

from reversal_engine import ReversalEngine
from trading_service import Candle, PatternRecognizer

_HERE = os.path.dirname(os.path.abspath(__file__))
OUT_FILE = os.path.join(_HERE, "exit_study.json")
API = "http://127.0.0.1:5000/api/scanner"

SYMBOLS = ["R_10", "R_25", "R_50", "R_75", "R_100",
           "1HZ10V", "1HZ25V", "1HZ50V", "1HZ75V", "1HZ100V"]
TIMEFRAMES = ["1m", "5m", "15m", "30m"]
COUNT, START, STEP, MAX_BARS = 200, 60, 2, 50
MIN_SIGNALS = 8

# Lowest multiplier Deriv accepts per symbol == the widest stop affordable.
MIN_MULT = {"R_10": 400, "1HZ10V": 400, "R_25": 160, "1HZ25V": 160,
            "R_50": 80, "1HZ50V": 80, "R_75": 50, "1HZ75V": 50,
            "R_100": 40, "1HZ100V": 40}

# label -> (stop_distance in % of price, target_distance in % of price)
# "atr*k" are resolved per signal from that signal's ATR; "lot_*" are fixed
# fractions of the stake, which at multiplier M equal 100/M % of price.
RULES = {
    "atr 1.5 / 6":      ("atr", 1.5, 6.0),
    "atr 1.0 / 4":      ("atr", 1.0, 4.0),
    "atr 3.0 / 12":     ("atr", 3.0, 12.0),
    "SL=lot TP=5x lot": ("stake", 100.0, 500.0),
    "SL=lot TP=2x lot": ("stake", 100.0, 200.0),
    "SL=lot TP=lot":    ("stake", 100.0, 100.0),
    "SL=half lot TP=5x": ("stake", 50.0, 500.0),
}


def fetch():
    body = json.dumps({"symbols": SYMBOLS, "timeframes": TIMEFRAMES,
                       "count": COUNT}).encode()
    req = urllib.request.Request(API, data=body,
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=300).read())["results"]


def to_candles(raw):
    out = []
    for c in raw or []:
        try:
            out.append(Candle(timestamp=int(c["epoch"]), open=float(c["open"]),
                              high=float(c["high"]), low=float(c["low"]),
                              close=float(c["close"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def atr_pct(candles, period=14):
    tail = candles[-period:]
    vals = [(c.high - c.low) / c.close * 100 for c in tail if c.close]
    return sum(vals) / len(vals) if vals else 0.0


def evaluate(candles, i, long, stop_move_pct, tp_move_pct):
    """P/L as a fraction of stake. stop_move_pct/tp_move_pct are % of PRICE."""
    entry = candles[i].close
    sd = entry * stop_move_pct / 100.0
    td = entry * tp_move_pct / 100.0
    if sd <= 0:
        return None
    for j in range(i + 1, min(i + 1 + MAX_BARS, len(candles))):
        if long:
            if candles[j].low <= entry - sd:
                return -stop_move_pct
            if candles[j].high >= entry + td:
                return tp_move_pct
        else:
            if candles[j].high >= entry + sd:
                return -stop_move_pct
            if candles[j].low <= entry - td:
                return tp_move_pct
    last = candles[min(i + MAX_BARS, len(candles) - 1)].close
    move = (last - entry) / entry * 100.0
    return move if long else -move


def run():
    t0 = time.time()
    engine = ReversalEngine()
    per_rule = {k: [] for k in RULES}          # list of (pct_move, mult)
    per_tf = {}
    for r in fetch():
        candles = to_candles(r.get("candles"))
        sym, tf = r.get("symbol"), r.get("timeframe")
        mult = MIN_MULT.get(sym)
        if not mult or len(candles) < START + 20:
            continue
        n_sig = 0
        for i in range(START, len(candles) - 3, STEP):
            window = candles[:i + 1]
            try:
                tdi = engine.tdi(window)
                pats = PatternRecognizer.detect_reversal_patterns(window)
                rev = engine.reversal(tdi, pats, window)
            except Exception:
                continue
            if rev not in ("BUY", "SELL"):
                continue
            atr = atr_pct(window)
            if atr <= 0:
                continue
            n_sig += 1
            long = rev == "BUY"
            for label, (kind, k_sl, k_tp) in RULES.items():
                if kind == "atr":
                    sm, tm = k_sl * atr, k_tp * atr
                else:                            # fraction of the STAKE
                    sm, tm = k_sl / mult, k_tp / mult
                out = evaluate(candles, i, long, sm, tm)
                if out is None:
                    continue
                pct_of_stake = out * mult          # exposure = stake x multiplier
                per_rule[label].append(pct_of_stake)
                per_tf.setdefault((label, tf), []).append(pct_of_stake)
        print(f"  {sym:>8} {tf:>4}: {n_sig} signals", flush=True)

    print(f"\n{'EXIT RULE':>19} {'n':>5} {'mean %stake':>12} {'median':>8} {'win%':>6} {'worst':>8}")
    summary = {}
    for label in RULES:
        v = per_rule[label]
        if not v:
            continue
        v_sorted = sorted(v)
        mean = sum(v) / len(v)
        win = sum(1 for x in v if x > 0) / len(v)
        summary[label] = {"n": len(v), "mean_pct": round(mean, 3),
                          "median_pct": round(v_sorted[len(v) // 2], 3),
                          "win_rate": round(win, 4), "worst_pct": round(min(v), 1)}
        print(f"{label:>19} {len(v):>5} {mean:>+11.2f}% {summary[label]['median_pct']:>+7.2f}% "
              f"{win * 100:>5.1f}% {summary[label]['worst_pct']:>+7.1f}%")

    if summary:
        best = max(summary, key=lambda k: summary[k]["mean_pct"])
        print(f"\nBEST RULE: {best} ({summary[best]['mean_pct']:+.2f}% of stake per signal)")
        print("NOTE: means are dominated by the widest rules' fat tails; the median and "
              "worst case matter more when the stop is the whole lot.")

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump({"generated_at": time.time(), "rules": summary,
                   "per_timeframe": {f"{k[0]}|{k[1]}": round(sum(v) / len(v), 3)
                                     for k, v in per_tf.items() if v}}, f, indent=1)
    print(f"\nwrote {OUT_FILE} in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    print("Exit-rule comparison on identical signals (multiplier = symbol minimum)")
    run()
