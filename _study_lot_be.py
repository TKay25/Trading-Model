"""Would the lot-size + break-even-only rule help or hurt? Measured, not guessed.

The rule: stop = the whole lot, and the ONLY adjustment is at break-even, so a
trade ends at -lot or at ~0.

The whole question is the RESCUE RATE: how often does a trade tick positive
(arming break-even) before it would have hit a stop? A trade that goes straight
down without ever ticking up rides all the way to -lot, and with SL=lot a
stop-out costs the FULL stake rather than the old 1.5xATR stop. So this rule is
better only if enough trades are rescued.

Counterfactual per closed path record (it has profit_min/max and its own
take_profit, so this needs no re-simulation of the market):
    profit_max >= tp   -> the target was reached      -> +tp
    profit_max >  0    -> armed, then returned to 0   -> ~0
    profit_max <= 0    -> never positive, rides down  -> -stake (the full lot)

NOTE this is an OPTIMISTIC bound: it assumes the break-even exit lands at exactly
0, whereas the real 2s poll typically closes a few cents under (measured -0.01 to
-0.02), and it ignores that the old ATR stop sometimes SAVED a trade that the
lot stop would let run to -stake.
"""
import json
from collections import defaultdict

PATH = "trade_paths.json"

with open(PATH, encoding="utf-8") as f:
    data = json.load(f)
recs = list((data or {}).values()) if isinstance(data, dict) else list(data or [])

closed = [r for r in recs
          if r.get("exit_reason") and r.get("profit_max") is not None
          and r.get("profit_min") is not None]
print(f"closed path records with an excursion envelope: {len(closed)}")

tp_missing = sum(1 for r in closed if not r.get("take_profit"))


def project(r):
    stake = float(r.get("stake") or 1.0)
    tp = float(r.get("take_profit") or 0.0)
    mx = float(r.get("profit_max") or 0.0)
    if tp > 0 and mx >= tp:
        return tp, "target"
    if mx > 0:
        return 0.0, "break-even (~0)"
    return -stake, "-lot (never ticked up)"


actual_tot = proj_tot = 0.0
buckets = defaultdict(lambda: {"n": 0, "actual": 0.0, "proj": 0.0, "rescue": 0,
                               "lot": 0, "tgt": 0})
per_sym = defaultdict(lambda: {"n": 0, "actual": 0.0, "proj": 0.0, "rescue": 0, "lot": 0, "tgt": 0})

for r in closed:
    a = float(r.get("profit_last") or 0.0)
    p, kind = project(r)
    actual_tot += a
    proj_tot += p
    for table, key in ((buckets, r.get("timeframe") or "?"),
                       (per_sym, r.get("symbol") or "?")):
        b = table[key]
        b["n"] += 1
        b["actual"] += a
        b["proj"] += p
        if kind.startswith("break-even"):
            b["rescue"] += 1
        elif kind.startswith("-lot"):
            b["lot"] += 1
        else:
            b["tgt"] += 1

n = len(closed) or 1
print(f"\nmissing take_profit on {tp_missing} record(s)")
print(f"\nACTUAL net over these records      : {actual_tot:+.2f}  "
      f"({actual_tot / n:+.3f}/trade)")
print(f"PROJECTED under lot + break-even   : {proj_tot:+.2f}  ({proj_tot / n:+.3f}/trade)")
print(f"  difference                       : {proj_tot - actual_tot:+.2f}")

tot_rescue = sum(b["rescue"] for b in buckets.values())
tot_lot = sum(b["lot"] for b in buckets.values())
tot_tgt = sum(b["tgt"] for b in buckets.values())
print(f"\nRESCUE RATE: {tot_rescue}/{len(closed)} = {100 * tot_rescue / n:.0f}% ticked positive "
      f"(break-even rescues them)")
print(f"  never ticked up -> -lot : {tot_lot} ({100 * tot_lot / n:.0f}%)")
print(f"  reached the target      : {tot_tgt} ({100 * tot_tgt / n:.0f}%)")

print("\n--- by timeframe (actual -> projected) ---")
for k, b in sorted(buckets.items(), key=lambda kv: kv[1]["proj"] - kv[1]["actual"]):
    print(f"  {k:>8} n={b['n']:>3}  {b['actual'] / b['n']:+.3f} -> {b['proj'] / b['n']:+.3f} "
          f"  rescue {100 * b['rescue'] / b['n']:>3.0f}%  lot {100 * b['lot'] / b['n']:>3.0f}%")

print("\n--- by symbol (worst projected first) ---")
for k, b in sorted(per_sym.items(), key=lambda kv: kv[1]["proj"] / kv[1]["n"])[:12]:
    print(f"  {k:>9} n={b['n']:>3}  {b['actual'] / b['n']:+.3f} -> {b['proj'] / b['n']:+.3f} "
          f"  rescue {100 * b['rescue'] / b['n']:>3.0f}%  lot {100 * b['lot'] / b['n']:>3.0f}%")
