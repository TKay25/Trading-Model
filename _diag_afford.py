"""Can a position be protected at all? The adoption/affordability test.

Protection requires  multiplier x (sl_atr_k x ATR%) <= risk cap for the timeframe.
If that fails, the code deliberately leaves the position UNTRACKED -- no stop,
no target -- and only Deriv's -100% auto-close can end it.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CAP = {"1m": 0.20, "5m": 0.30, "15m": 0.55, "30m": 0.80}
K_SL, FLOOR = 1.5, 0.05
MIN_MULT = {"R_10": 400, "1HZ10V": 400, "R_25": 160, "1HZ25V": 160,
            "R_50": 80, "1HZ50V": 80, "R_75": 50, "1HZ75V": 50,
            "R_100": 40, "1HZ100V": 40}

p = os.path.join(HERE, "atr_cache.json")
print("atr_cache.json exists:", os.path.exists(p))
cache = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
print("entries:", len(cache))
for k in list(cache)[:5]:
    print("  sample key:", repr(k), "->", cache[k])

print("\n--- affordability: risk = multiplier x k_sl x ATR (as a fraction of stake) ---")
print(f"{'market':>12} {'tf':>5} {'ATR%':>8} {'risk':>8} {'cap':>6}  verdict")
rows = []
for k, v in cache.items():
    if isinstance(v, dict):
        sym = v.get("symbol") or (k.split("|")[0] if "|" in k else k)
        tf = v.get("timeframe") or (k.split("|")[1] if "|" in k else "?")
        atr = v.get("atr_pct") or v.get("atr")
    else:
        # flat cache shape: {"R_10|1m": 0.0187, ...} -> the VALUE is the ATR%
        parts = str(k).split("|")
        sym, tf, atr = parts[0], (parts[1] if len(parts) > 1 else "?"), v
    try:
        atr = float(atr)
    except (TypeError, ValueError):
        continue
    cap = CAP.get(tf)
    mult = MIN_MULT.get(sym)
    if not cap or not mult:
        continue
    stop_move = max(K_SL * atr, FLOOR)
    risk = mult * stop_move / 100.0
    rows.append((sym, tf, atr, risk, cap, risk <= cap))

for sym, tf, atr, risk, cap, ok in sorted(rows, key=lambda r: (r[0], r[1])):
    print(f"{sym:>12} {tf:>5} {atr:>7.4f}% {risk * 100:>7.1f}% {cap * 100:>5.0f}%  "
          f"{'OK' if ok else 'REFUSED -> UNPROTECTED'}")

print("\n--- how close is each to flipping? (ATR multiple that breaks the cap) ---")
for sym, tf, atr, risk, cap, ok in sorted(rows, key=lambda r: (r[0], r[1])):
    if atr <= 0:
        continue
    max_atr = cap * 100.0 / (MIN_MULT.get(sym, 1) * K_SL)
    print(f"{sym:>12} {tf:>5}: breaks if 5m/that-tf ATR exceeds {max_atr:.4f}% "
          f"(now {atr:.4f}%, headroom {max_atr / atr:.2f}x)")
