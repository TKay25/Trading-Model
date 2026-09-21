"""SWEEP the exit geometry (k_sl, k_tp) and find what actually pays.

WHY THIS EXISTS
---------------
The Backtest button measures ONE geometry on ONE chart. It reported, on R_75 5m:
89% of signals hit the stop, 0% reached the target, expectancy -0.802R — with a
53% DIRECTIONAL hit rate. So the entries are close to a coin flip (fine) and the
1.5xATR / 6xATR bracket is what loses the money: a 6xATR target has to appear
before a 1.5xATR adverse move, and it almost never does.

This answers the obvious follow-up question — "so what bracket DOES work?" —
over real history, for every market and timeframe, with the SAME signal engine
the bot trades (`reversal_engine.ReversalEngine`, verified at parity with the JS
one), and never seeing the future.

DESIGN NOTE THAT KEEPS IT FAST: the signal engine is run ONCE per (symbol,
timeframe) and every signal is cached as (bar index, direction, ATR%). The grid
is then swept over that cache, which is only a high/low path walk. Re-running the
engine per candidate would be ~70x the work for identical signals.
"""
import json
import time
import urllib.request

from reversal_engine import ReversalEngine
from trading_service import Candle, PatternRecognizer

API = "http://127.0.0.1:5000/api/scanner"
# The workspace lives behind a corporate proxy that also intercepts loopback.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

SYMBOLS = ["R_10", "R_25", "R_50", "R_75", "R_100",
           "1HZ10V", "1HZ25V", "1HZ50V", "1HZ75V", "1HZ100V"]
TIMEFRAMES = ["5m", "15m"]
COUNT = 400          # candles per pair (more history = fewer signals missed)
START = 60           # warmup bars before evaluating
STEP = 3             # evaluate every Nth bar
MAX_BARS = 50        # bars allowed to reach stop/target
MIN_SIGNALS = 8      # a pair needs this many signals to be reported

K_SL_GRID = (0.5, 0.75, 1.0, 1.5, 2.0, 3.0)
K_TP_GRID = (0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0)
BASELINE = (1.5, 6.0)          # what the bot currently runs
OUT_FILE = "geometry_sweep.json"


def fetch(symbols, timeframes, count=COUNT):
    body = json.dumps({"symbols": symbols, "timeframes": timeframes,
                       "count": count}).encode()
    req = urllib.request.Request(API, data=body,
                                 headers={"Content-Type": "application/json"})
    return json.loads(OPENER.open(req, timeout=600).read())["results"]


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


def simulate(candles, i, long, atr, k_sl, k_tp):
    """R for a trade entered at close[i]. Stop checked FIRST (conservative)."""
    entry = candles[i].close
    stop_d = entry * k_sl * atr / 100.0
    tp_d = entry * k_tp * atr / 100.0
    if stop_d <= 0 or tp_d <= 0 or entry <= 0:
        return None
    for j in range(i + 1, min(i + 1 + MAX_BARS, len(candles))):
        if long:
            if candles[j].low <= entry - stop_d:
                return -1.0
            if candles[j].high >= entry + tp_d:
                return k_tp / k_sl
        else:
            if candles[j].high >= entry + stop_d:
                return -1.0
            if candles[j].low <= entry - tp_d:
                return k_tp / k_sl
    last = candles[min(i + MAX_BARS, len(candles) - 1)].close
    move = (last - entry) if long else (entry - last)
    return move / stop_d


def mean(v):
    return sum(v) / len(v) if v else None


def main():
    t0 = time.time()
    engine = ReversalEngine()
    print(f"sweeping {len(K_SL_GRID)}x{len(K_TP_GRID)} geometries over "
          f"{len(SYMBOLS)} symbols x {TIMEFRAMES}, {COUNT} candles/pair\n", flush=True)

    cached = []          # (symbol, tf, candles, [(i, long, atr)])
    for r in fetch(SYMBOLS, TIMEFRAMES):
        sym, tf = r.get("symbol"), r.get("timeframe")
        candles = to_candles(r.get("candles"))
        if len(candles) < START + 20:
            print(f"  {sym:>8} {tf:>4}: only {len(candles)} candles, skipped", flush=True)
            continue
        sigs = []
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
            a = atr_pct(window)
            if a > 0:
                sigs.append((i, rev == "BUY", a))
        cached.append((sym, tf, candles, sigs))
        print(f"  {sym:>8} {tf:>4}: {len(sigs)} signals", flush=True)

    total_sig = sum(len(s) for _, _, _, s in cached)
    print(f"\ncollected {total_sig} signals in {time.time() - t0:.0f}s; sweeping…\n", flush=True)

    def score(sigs, candles, k_sl, k_tp):
        rs = [simulate(candles, i, long, a, k_sl, k_tp) for i, long, a in sigs]
        rs = [x for x in rs if x is not None]
        if not rs:
            return None, 0, None
        return mean(rs), len(rs), sum(1 for x in rs if x > 0) / len(rs)

    rows = []
    for sym, tf, candles, sigs in cached:
        if not sigs:
            continue
        base_r, base_n, base_w = score(sigs, candles, *BASELINE)
        best = None
        for k_sl in K_SL_GRID:
            for k_tp in K_TP_GRID:
                if k_tp < k_sl:
                    continue                     # never a worse-than-1:1 bracket
                r, n, w = score(sigs, candles, k_sl, k_tp)
                if r is None:
                    continue
                if best is None or r > best[0]:
                    best = (r, k_sl, k_tp, n, w)
        rows.append({"symbol": sym, "timeframe": tf, "n": len(sigs),
                     "baseline_k": BASELINE, "baseline_R": base_r, "baseline_win": base_w,
                     "best_k": (best[1], best[2]), "best_R": best[0],
                     "best_win": best[4]})
        print(f"  {sym:>8} {tf:>4} n={len(sigs):>3} | 1.5/6 -> {base_r:+.3f}R "
              f"(win {base_w * 100:>4.0f}%) | best {best[1]}/{best[2]} -> {best[0]:+.3f}R "
              f"(win {best[4] * 100:>4.0f}%)", flush=True)

    # Which single geometry is best POOLED across everything?
    print("\n--- pooled ranking (all pairs, weighted by signals) ---", flush=True)
    ranked = []
    for k_sl in K_SL_GRID:
        for k_tp in K_TP_GRID:
            if k_tp < k_sl:
                continue
            rs, n = [], 0
            for _, _, candles, sigs in cached:
                r, cnt, _ = score(sigs, candles, k_sl, k_tp)
                if r is not None:
                    rs.append(r * cnt)
                    n += cnt
            if n:
                ranked.append((sum(rs) / n, k_sl, k_tp, n))
    ranked.sort(reverse=True)
    for r, k_sl, k_tp, n in ranked[:8]:
        print(f"  {k_sl:>4} / {k_tp:<4} -> {r:+.4f}R  (n={n})", flush=True)
    cur = next((x for x in ranked if x[1] == BASELINE[0] and x[2] == BASELINE[1]), None)
    if cur:
        print(f"  CURRENT {BASELINE[0]}/{BASELINE[1]} -> {cur[0]:+.4f}R  (rank "
              f"{ranked.index(cur) + 1}/{len(ranked)})", flush=True)

    payload = {"generated_at": time.time(),
               "config": {"candles": COUNT, "step": STEP, "max_bars": MAX_BARS,
                          "k_sl_grid": list(K_SL_GRID), "k_tp_grid": list(K_TP_GRID),
                          "baseline": list(BASELINE)},
               "pairs": rows,
               "pooled": [{"k_sl": k_sl, "k_tp": k_tp, "mean_R": r, "n": n}
                          for r, k_sl, k_tp, n in ranked]}
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1)
    print(f"\nwrote {OUT_FILE} in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
