"""Test restore_open(): the restart-orphan fix.

Proves that positions persisted in trade_paths.json are re-registered at boot
with EXACTLY the SL/TP they were opened with, that it is idempotent, and that it
cannot invent a stop for a record that never had one.
"""
import sys

from position_monitor import PositionMonitor, trade_paths

OPEN = {cid: r for cid, r in trade_paths.items()
        if isinstance(r, dict) and not r.get("t_close")}
ENFORCEABLE = {cid: r for cid, r in OPEN.items()
               if (r.get("stop_loss") or 0) or (r.get("take_profit") or 0)}

print(f"open records: {len(OPEN)}   enforceable (have SL/TP): {len(ENFORCEABLE)}")
for cid, r in list(OPEN.items())[:5]:
    print(f"  {cid} {r.get('symbol')} {r.get('timeframe')} "
          f"sl={r.get('stop_loss')} tp={r.get('take_profit')} "
          f"samples={r.get('samples')}")

m = PositionMonitor()
n = m.restore_open()
print(f"\nrestored: {n}")

ok = True
if n != len(ENFORCEABLE):
    print(f"FAIL: restored {n} but {len(ENFORCEABLE)} records are enforceable")
    ok = False

mismatch = []
for cid, r in ENFORCEABLE.items():
    lim = m._limits.get(cid)
    if lim is None:
        mismatch.append((cid, "not tracked"))
        continue
    if abs(lim["stop_loss"] - float(r.get("stop_loss") or 0)) > 1e-9:
        mismatch.append((cid, f"sl {lim['stop_loss']} != {r.get('stop_loss')}"))
    if abs(lim["take_profit"] - float(r.get("take_profit") or 0)) > 1e-9:
        mismatch.append((cid, f"tp {lim['take_profit']} != {r.get('take_profit')}"))
if mismatch:
    print("FAIL mismatches:", mismatch[:10])
    ok = False
else:
    print("all restored stops/targets match the persisted values exactly")

# Idempotent: a second call must not double-track or duplicate anything.
n2 = m.restore_open()
print(f"second call restored: {n2} (must be 0)")
if n2 != 0:
    ok = False

# A record with NO sl/tp (observational manual trade) must NOT be tracked.
no_geom = [cid for cid, r in OPEN.items()
           if not (r.get("stop_loss") or 0) and not (r.get("take_profit") or 0)]
invented = [cid for cid in no_geom if m.is_tracked(cid)]
print(f"records with no SL/TP: {len(no_geom)}   wrongly tracked: {len(invented)}")
if invented:
    print("FAIL: invented tracking for records with no geometry")
    ok = False

# The dynamic stop must be reinstated from _live when it was persisted, instead
# of resetting to -stop_loss (which would give back locked-in profit).
with_live = [cid for cid, r in ENFORCEABLE.items() if isinstance(r.get("_live"), dict)]
raised = [cid for cid in with_live
          if m._limits[cid]["stop"] > -m._limits[cid]["stop_loss"] + 1e-9]
print(f"records carrying _live: {len(with_live)}   with a raised (break-even/trail) stop: {len(raised)}")
for cid in raised[:5]:
    r = ENFORCEABLE[cid]
    print(f"  {cid}: persisted stop {r['_live'].get('stop')} "
          f"vs -stop_loss {-float(r.get('stop_loss') or 0)} -> using "
          f"{m._limits[cid]['stop']}")

print(f"tracked_count: {m.tracked_count()}")
print("\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
