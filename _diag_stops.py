"""Why do some trades lose more than their stop?

Cross-references the trade ledger (trade_results.json) with the path records
(trade_paths.json) to measure how many positions the position monitor actually
sampled. samples == 0 means the monitor NEVER read that contract, so no
stop-loss could possibly have been enforced on it.
"""
import json
import os
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
WANT = ["13741760179", "13741382299", "13741126279", "13740743959"]


def load(name):
    p = os.path.join(HERE, name)
    if not os.path.exists(p):
        return None
    return json.load(open(p, encoding="utf-8"))


paths_raw = load("trade_paths.json") or {}
paths = {}
for cid, rec in paths_raw.items():
    if isinstance(rec, dict):
        rec.setdefault("cid", cid)
        paths[str(cid)] = rec
print(f"path records: {len(paths)}")

led = load("trade_results.json")
led_rows = []
if isinstance(led, dict):
    for k, v in led.items():
        if isinstance(v, dict):
            v.setdefault("cid", k)
            led_rows.append(v)
        elif isinstance(v, list):
            led_rows.extend(v)
elif isinstance(led, list):
    led_rows = led
print(f"ledger entries: {len(led_rows)}")
if led_rows:
    print("ledger keys sample:", sorted(led_rows[0].keys())[:20])

# ---------------------------------------------------------------- sampling
print("\n--- monitor sampling coverage (all path records) ---")
zero = [r for r in paths.values() if not r.get("samples")]
sample_counts = Counter(r.get("samples") for r in paths.values())
for s, c in sorted(sample_counts.items(), key=lambda kv: (kv[0] is None, kv[0])):
    print(f"  samples={str(s):>6}: {c}")
print(f"  NEVER sampled (samples==0 or None): {len(zero)} / {len(paths)}")

print("\n--- never-sampled by exit reason ---")
for r, c in Counter(x.get("exit_reason") for x in zero).most_common():
    print(f"  {str(r):>18}: {c}")

print("\n--- never-sampled by timeframe ---")
for r, c in Counter(x.get("timeframe") for x in zero).most_common():
    print(f"  {str(r):>18}: {c}")

print("\n--- hold time for never-sampled vs sampled (seconds) ---")
for label, group in (("sampled", [r for r in paths.values() if r.get("samples")]),
                     ("NEVER", zero)):
    hs = [r.get("hold_s") for r in group if r.get("hold_s")]
    if hs:
        hs_sorted = sorted(hs)
        print(f"  {label:>7}: n={len(hs)} median={hs_sorted[len(hs)//2]:.0f}s "
              f"max={max(hs):.0f}s")

# ------------------------------------------------------- ledger vs paths
if led_rows:
    def key(row):
        return str(row.get("cid") or row.get("contract_id") or "")
    led_ids = {key(r) for r in led_rows if key(r)}
    missing = led_ids - set(paths)
    print(f"\n--- ledger contracts with NO path record: {len(missing)} / {len(led_ids)} ---")
    # show the ones from the question
    for w in WANT:
        row = next((r for r in led_rows if key(r) == w), None)
        print(f"\n  {w}: in_paths={w in paths} in_ledger={row is not None}")
        if row:
            print("   ", json.dumps(row, default=str)[:500])

# ------------------------------------------------- how the losers exited
print("\n--- P/L of the contracts the user flagged ---")
for w in WANT:
    r = paths.get(w)
    if r:
        print(f"  {w}: samples={r.get('samples')} exit={r.get('exit_reason')} "
              f"sl={r.get('stop_loss')} min={r.get('profit_min_pct')} "
              f"final={r.get('final_pct')} hold={r.get('hold_s')}")
    else:
        print(f"  {w}: NO PATH RECORD")
