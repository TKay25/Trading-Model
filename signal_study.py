"""Raw signal-edge study: does a reversal signal produce a favourable move?

WHY THIS EXISTS
---------------
The bot's own path data is accumulating slowly, and the first 26 closed trades
under the ATR geometry show 0 winners with a best-ever favourable excursion of
+6% of stake. That points at the ENTRIES rather than the exits, but n=26 is too
small to conclude. This script measures the same question directly on historical
candles with NO exits, NO spread and NO execution: after a reversal signal
appears, how far does price actually move, and would the live geometry
(stop 1.5xATR / target 6xATR) have won or lost?

METHOD
------
For each (symbol, timeframe) and each bar i in the history:
  1. run the SAME signal engine the live bot uses on candles[:i+1]
     (ReversalEngine.tdi -> PatternRecognizer -> ReversalEngine.reversal)
  2. if it says BUY/SELL, enter at close[i]
  3. walk forward up to MAX_BARS bars using the real high/low path:
       stop   = 1.5 x ATR%(at i) of price   (checked FIRST within a bar -> conservative)
       target = 6.0 x ATR%(at i)
  4. record the outcome in R multiples (+6/1.5 = +4R target, -1R stop,
     otherwise the unresolved move expressed in R)

READING THE OUTPUT
------------------
  meanR  = expectancy per signal in R. This is the whole question:
           > 0 means the entry has edge, <= 0 means no exit tuning can save it.
  win%   = needs ~20% at a 1:4 payoff just to break even (shown for reference).
Only (symbol, timeframe) pairs with enough signals are reported; the pooled row
is the overall verdict. Writes signal_study.json for later comparison.
"""
import json
import os
import time
import urllib.request

from reversal_engine import ReversalEngine
from trading_service import Candle, PatternRecognizer

_HERE = os.path.dirname(os.path.abspath(__file__))
OUT_FILE = os.path.join(_HERE, "signal_study.json")

API = "http://127.0.0.1:5000/api/scanner"
SYMBOLS = ["R_10", "R_25", "R_50", "R_75", "R_100",
           "1HZ10V", "1HZ25V", "1HZ50V", "1HZ75V", "1HZ100V"]
TIMEFRAMES = ["1m", "5m", "15m", "30m"]
COUNT = 200          # candles per symbol/timeframe
START = 60           # bar index to start evaluating from (needs warmup)
STEP = 2             # evaluate every Nth bar (1 = every bar, slower)
K_SL, K_TP = 1.5, 6.0
MAX_BARS = 50        # give up after this many bars (unresolved -> mark to market)
MIN_SIGNALS = 8      # report a pair only when it has at least this many signals


def fetch(symbols, timeframes, count=COUNT):
    body = json.dumps({"symbols": symbols, "timeframes": timeframes,
                       "count": count}).encode()
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


def simulate(candles, i, direction, atr):
    """R multiple for a trade entered at close[i] in `direction`."""
    entry = candles[i].close
    stop_d = entry * K_SL * atr / 100.0
    tp_d = entry * K_TP * atr / 100.0
    if stop_d <= 0:
        return None
    long = direction == "BUY"
    for j in range(i + 1, min(i + 1 + MAX_BARS, len(candles))):
        if long:
            # stop checked first inside the bar: conservative when both are touched
            if candles[j].low <= entry - stop_d:
                return -1.0
            if candles[j].high >= entry + tp_d:
                return K_TP / K_SL
        else:
            if candles[j].high >= entry + stop_d:
                return -1.0
            if candles[j].low <= entry - tp_d:
                return K_TP / K_SL
    last = candles[min(i + MAX_BARS, len(candles) - 1)].close
    move = (last - entry) if long else (entry - last)
    return move / stop_d


def run():
    t0 = time.time()
    engine = ReversalEngine()
    results = []
    for r in fetch(SYMBOLS, TIMEFRAMES):
        candles = to_candles(r.get("candles"))
        sym, tf = r.get("symbol"), r.get("timeframe")
        if len(candles) < START + 20:
            print(f"  {sym:>8} {tf:>4}: only {len(candles)} candles, skipped")
            continue
        rs, n_buy, n_sell = [], 0, 0
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
            outcome = simulate(candles, i, rev, atr)
            if outcome is None:
                continue
            rs.append(outcome)
            n_buy += rev == "BUY"
            n_sell += rev == "SELL"
        if rs:
            wins = [x for x in rs if x > 0]
            row = {"symbol": sym, "timeframe": tf, "n": len(rs),
                   "mean_R": round(sum(rs) / len(rs), 4),
                   "win_rate": round(len(wins) / len(rs), 4),
                   "buy": n_buy, "sell": n_sell,
                   "total_R": round(sum(rs), 2)}
            results.append(row)
            print(f"  {sym:>8} {tf:>4} n={len(rs):>4} meanR={row['mean_R']:>+7.3f} "
                  f"win={row['win_rate'] * 100:>5.1f}%  (buy {n_buy} / sell {n_sell})",
                  flush=True)

    pooled = [r for r in results if r["n"] >= MIN_SIGNALS]
    if pooled:
        tot_n = sum(r["n"] for r in pooled)
        wmean = sum(r["mean_R"] * r["n"] for r in pooled) / tot_n
        wwin = sum(r["win_rate"] * r["n"] for r in pooled) / tot_n
        verdict = ("EDGE (positive expectancy)" if wmean > 0.02
                   else "NO EDGE — no exit tuning can fix a negative entry"
                   if wmean < 0 else "INCONCLUSIVE (essentially zero)")
        print(f"\nPOOLED {tot_n} signals across {len(pooled)} pairs | "
              f"mean {wmean:+.4f} R | win {wwin * 100:.1f}% | {verdict}")
        print(f"break-even win rate at 1:4 payoff = 20.0%")
    else:
        wmean = None
        print("\nToo few signals for a pooled verdict.")

    payload = {"generated_at": time.time(), "config": {"k_sl": K_SL, "k_tp": K_TP,
                                                       "max_bars": MAX_BARS, "step": STEP,
                                                       "candles": COUNT},
               "pairs": results, "min_signals": MIN_SIGNALS}
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1)
    print(f"\nwrote {OUT_FILE} ({len(results)} pairs with signals) in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    print("Raw signal-edge study — stop 1.5xATR / target 6xATR, entry at bar close")
    print(f"{'symbol':>8} {'tf':>4}  {'n':>4}   meanR   win%")
    run()
