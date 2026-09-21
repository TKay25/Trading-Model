"""Can exits be ATTRIBUTED? Group path records by exit_reason and test whether
restart-induced exits are distinguishable from genuine stop-outs.

Also measures the big blind spot: trades that have NO path record at all (they
existed only in the ledger), which any path-based profitability read would omit.
"""
import json
import os
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    p = os.path.join(HERE, name)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


paths = load("trade_paths.json") or {}
recs = [v for v in paths.values() if isinstance(v, dict)]
closed = [r for r in recs if r.get("t_close")]
print(f"path records: {len(recs)}  closed: {len(closed)}  open: {len(recs) - len(closed)}")

# ---------------------------------------------------------------- 1. reasons
print("\n--- 1. exit_reason groups (the attribution I DO have) ---")
print(f"{'exit_reason':>20} {'n':>5} {'mean_final%':>12} {'med_final%':>11} {'mean_hold_s':>12} {'adopted':>8}")
for reason, n in Counter(r.get("exit_reason") for r in closed).most_common():
    grp = [r for r in closed if r.get("exit_reason") == reason]
    fin = [r["profit_final_pct"] for r in grp if r.get("profit_final_pct") is not None]
    hold = [r["hold_s"] for r in grp if r.get("hold_s") is not None]
    ad = sum(1 for r in grp if r.get("adopted"))
    print(f"{str(reason):>20} {n:>5} "
          f"{(sum(fin)/len(fin)*100 if fin else float('nan')):>11.1f}% "
          f"{(sorted(fin)[len(fin)//2]*100 if fin else float('nan')):>10.1f}% "
          f"{(sum(hold)/len(hold) if hold else float('nan')):>12.0f} {ad:>8}")

# ------------------------------------------- 2. restart-induced stop-outs?
# An ADOPTED position's record starts at ADOPTION time, so a stop that fires
# seconds later is the adoption-liquidating-the-position signature.
print("\n--- 2. can restart-induced liquidations be separated from real stops? ---")
stops = [r for r in closed if r.get("exit_reason") == "stop_loss"]
adopted_stops = [r for r in stops if r.get("adopted")]
normal_stops = [r for r in stops if not r.get("adopted")]
print(f"stop_loss exits: {len(stops)}  (adopted at open: {len(adopted_stops)}, "
      f"never adopted: {len(normal_stops)})")


def hold_stats(name, grp):
    hs = sorted(r["hold_s"] for r in grp if r.get("hold_s") is not None)
    if not hs:
        print(f"  {name}: no hold data")
        return
    print(f"  {name}: n={len(hs)} min={hs[0]:.0f}s median={hs[len(hs)//2]:.0f}s "
          f"p90={hs[int(len(hs)*0.9)]:.0f}s max={hs[-1]:.0f}s")


hold_stats("adopted stop_loss ", adopted_stops)
hold_stats("normal  stop_loss ", normal_stops)

instant = [r for r in adopted_stops if (r.get("hold_s") or 0) < 120]
print(f"  adopted stops firing within 120s of adoption: {len(instant)}")
for r in instant[:10]:
    print(f"    {r.get('cid')} {r.get('symbol')} {r.get('timeframe')} "
          f"hold={r.get('hold_s')}s final={r.get('profit_final_pct')} "
          f"stop={r.get('stop_loss')}")

# ------------------------------------------------- 3. the no-record blind spot
print("\n--- 3. trades with NO path record (invisible to a path-based check) ---")
led = load("trade_results.json") or {}
# NOTE: in trade_results.json the contract id is the DICT KEY, not a field.
# Reading r["cid"] here yields None for every row and fabricates a "100% missing"
# result — the exact artefact this file's own docstring warns about.
rows = []
if isinstance(led, dict):
    for k, v in led.items():
        if isinstance(v, dict):
            v.setdefault("cid", k)
            rows.append(v)
elif isinstance(led, list):
    rows = led
print(f"ledger entries: {len(rows)}   path records: {len(recs)}")
have = set()
for r in recs:
    if r.get("cid") is not None:
        have.add(str(r["cid"]))
missing = [r for r in rows if str(r.get("cid") or r.get("contract_id")) not in have]
print(f"ledger rows with no path record: {len(missing)}")

full_loss = [r for r in rows if isinstance(r.get("profit"), (int, float)) and r["profit"] <= -0.95]
fl_missing = [r for r in full_loss if str(r.get("cid")) not in have]
print(f"FULL-STAKE losses (profit <= -0.95) in ledger: {len(full_loss)}")
print(f"  ...of which have NO path record: {len(fl_missing)} "
      f"({(len(fl_missing)/len(full_loss)*100 if full_loss else 0):.0f}%)")
print("  -> a path-only profitability read silently DROPS these losses.")

auto_closed = [r for r in rows if r.get("sell_price") in ("0.00", 0, 0.0)]
print(f"  ledger rows with sell_price 0.00 (Deriv -100% close): {len(auto_closed)}, "
      f"no path record: {sum(1 for r in auto_closed if str(r.get('cid')) not in have)}")

# ------------------------------------------------------ 4. signature summary
print("\n--- 4. what a profitability check must exclude ---")
boot = [r for r in closed if r.get("exit_reason") in ("closed_while_down", "vanished")]
print(f"  boot/reconcile artefacts in path data: {len(boot)}")
print(f"  adoption-induced stop-outs (adopted + hold<120s): {len(instant)}")
print(f"  untracked full-stake losses only in the ledger: {len(fl_missing)}")
