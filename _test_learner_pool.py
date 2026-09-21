"""Verify the strategy learner can actually FIT now (it never could before).

Before the pooling change every group was keyed "SYMBOL|tf" and MIN_TRADES=30,
so with ~205 closed path records over 40 pairs every single group reported
"insufficient_data" and the learner had never once changed a parameter while
advertising itself as running.

This runs the real fit on the REAL records and prints each decision, grouped by
level, so the pooling can be judged on the actual data rather than on a claim.
"""
import json
from collections import Counter

import strategy_learner as sl

recs = sl.load_records()
print(f"closed path records available: {len(recs)}")
tf_counts = Counter(r.get("timeframe") for r in recs)
sym_counts = Counter(r.get("symbol") for r in recs)
print("by timeframe:", dict(tf_counts))
print("distinct symbols:", len(sym_counts))

result = sl.fit_all(records=recs, cfg={"risk_by_tf": {"1m": 0.20, "5m": 0.30,
                                                      "15m": 0.55, "30m": 0.0},
                                       "sl_atr_k": 1.5, "tp_atr_k": 6.0})
print(f"\ngroups fitted: {len(result['groups'])}  (was 40 x 'insufficient_data')")
print("params promoted:", json.dumps(result["params"]))
print("skip (pruned):", result["skip"])

print("\n--- every decision ---")
statuses = Counter()
for key, d in sorted(result["groups"].items()):
    statuses[d.get("status")] += 1
    extra = ""
    if d.get("status") == "promote":
        extra = f" -> {d.get('proposed')} fit_ev={d.get('fit_ev_pct')} oos={d.get('oos_ev_pct')}"
    elif d.get("status") == "prune":
        extra = f"  (best rule EV {d.get('fit_ev_pct')} <= 0)"
    print(f"  {key:>14}  n={d.get('n'):>3}  pooled={str(d.get('pooled')):>5}  "
          f"{d.get('status'):<18}{extra}")
print("\nstatus tally:", dict(statuses))

# The trader asks for exactly "SYMBOL|tf" - prove the pooled keys are readable.
learner = sl.StrategyLearner(cfg_fn=lambda: {}, enabled=False)
learner._params = result["params"]
learner._skip = set(result["skip"])
print("global_verdict:", result.get("global_verdict"))
probe = [("R_25", "5m"), ("NOPE", "9m"), ("R_25", "1m"), ("R_25", "30m")]
print("\n--- lookup resolution (params_for / is_pruned) ---")
print("    (a '*|tf' prune must apply to that tf only; '*|*' must apply to NOTHING)")
for sym, tf in probe:
    print(f"  {sym}|{tf}: params={learner.params_for(sym, tf)} pruned={learner.is_pruned(sym, tf)}")
print("\nDONE")
