"""Ad-hoc: inspect why specific trades lost more than their recorded stop."""
import json
import os
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
WANT = {"13741760179", "13741382299", "13741126279", "13740743959"}

data = json.load(open(os.path.join(HERE, "trade_paths.json"), encoding="utf-8"))
# trade_paths.json is a dict keyed by contract-id STRING -> record.
recs = []
if isinstance(data, dict):
    for cid, rec in data.items():
        if isinstance(rec, dict):
            rec.setdefault("cid", cid)
            recs.append(rec)
elif isinstance(data, list):
    recs = [r for r in data if isinstance(r, dict)]
print("total path records:", len(recs))

print("\n--- exit reason counts ---")
for r, c in Counter(x.get("exit_reason") for x in recs).most_common():
    print(f"  {str(r):>18}: {c}")

print("\n--- the 4 contracts in question ---")
for x in recs:
    if str(x.get("cid")) in WANT:
        print(json.dumps({k: x.get(k) for k in (
            "cid", "symbol", "timeframe", "direction", "signal_strength", "stake",
            "multiplier", "adopted", "atr_pct", "stop_move_pct", "risk_pct",
            "stop_loss", "take_profit", "hold_s", "samples",
            "profit_first", "profit_min", "profit_max", "profit_last",
            "exit_reason", "profit_min_pct", "profit_max_pct", "final_pct")},
            indent=1, default=str))

print("\n--- worst 12 closed by final P/L ---")
closed = [x for x in recs if x.get("exit_reason")]
closed.sort(key=lambda x: (x.get("final_pct") if x.get("final_pct") is not None else 0))
for x in closed[:12]:
    print(f"  {str(x.get('symbol')):>8} {str(x.get('timeframe')):>4} "
          f"reason={str(x.get('exit_reason')):>16} sl={x.get('stop_loss')} "
          f"min={x.get('profit_min_pct')} final={x.get('final_pct')} "
          f"risk_pct={x.get('risk_pct')}")

print("\n--- stop_loss vs actual final: violations ---")
viol = 0
tot = 0
for x in closed:
    sl = x.get("stop_loss")
    fin = x.get("final_pct")
    if sl is None or fin is None or x.get("exit_reason") != "stop_loss":
        continue
    tot += 1
    if fin < -(sl * 100) - 1.0:      # final worse than the stop by >1pp of stake
        viol += 1
        print(f"  {x.get('cid')} {x.get('symbol')} {x.get('timeframe')} "
              f"stop=-{sl * 100:.1f}% final={fin:.1f}% overshoot={fin + sl * 100:.1f}pp")
print(f"stop_loss exits: {tot}, overshot by >1pp: {viol}")
