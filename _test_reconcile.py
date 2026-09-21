"""Test the restart-orphan fix: restore_open() + drop_missing() reconciliation.

PART 1 runs against the REAL trade_paths.json but is strictly READ-ONLY.
PART 2 swaps in synthetic records and a TEMP file before anything flushes, so
the real dataset can never be overwritten by the test.
"""
import os
import sys
import tempfile

import position_monitor as pm

# ------------------------------------------------------------------ part 1
OPEN = {cid: r for cid, r in pm.trade_paths.items()
        if isinstance(r, dict) and not r.get("t_close")}
ENFORCEABLE = {cid: r for cid, r in OPEN.items()
               if (r.get("stop_loss") or 0) or (r.get("take_profit") or 0)}

print("=== PART 1: real data (read-only) ===")
print(f"path records: {len(pm.trade_paths)}   open: {len(OPEN)}   enforceable: {len(ENFORCEABLE)}")
for c, r in list(OPEN.items())[:6]:
    print(f"  {c} {r.get('symbol')} {r.get('timeframe')} "
          f"sl={r.get('stop_loss')} tp={r.get('take_profit')} samples={r.get('samples')}")

m = pm.PositionMonitor()
n = m.restore_open()
ok = n == len(ENFORCEABLE)
print(f"restored: {n} (expected {len(ENFORCEABLE)})")

bad = []
for cid, r in ENFORCEABLE.items():
    lim = m._limits.get(cid)
    if lim is None:
        bad.append((cid, "not tracked"))
    elif (abs(lim["stop_loss"] - float(r.get("stop_loss") or 0)) > 1e-9
          or abs(lim["take_profit"] - float(r.get("take_profit") or 0)) > 1e-9):
        bad.append((cid, "geometry mismatch"))
print("geometry mismatches:", bad or "none")
ok = ok and not bad

n2 = m.restore_open()
print(f"idempotent re-run: {n2} (expected 0)")
ok = ok and n2 == 0
print("(part 1 performed no writes)")

# ------------------------------------------------------------------ part 2
print("\n=== PART 2: sandboxed reconciliation ===")
tmp = os.path.join(tempfile.gettempdir(), "pm_test_paths.json")
if os.path.exists(tmp):
    os.remove(tmp)
pm.PATHS_FILE = tmp          # redirect BEFORE anything can flush
pm._last_save = 0.0


def rec(sym, sl, tp, tf="5m", close=False):
    r = {"cid": 0, "symbol": sym, "stop_loss": sl, "take_profit": tp,
         "timeframe": tf, "stake": 1.0, "samples": 3, "t_open": 1.0,
         "exit_reason": None, "t_close": None}
    if close:
        r["t_close"] = 2.0
        r["exit_reason"] = "stop_loss"
    return r


pm.trade_paths = {
    1001: rec("R_10", 0.2, 5.0),               # still open at Deriv -> restore
    1002: rec("R_25", 0.3, 1.2),               # settled while down  -> drop
    1003: rec("R_50", 0.0, 0.0),               # no geometry (manual) -> never tracked
    1004: rec("R_75", 0.1, 0.4, close=True),   # already closed -> ignored
}

m2 = pm.PositionMonitor()
n3 = m2.restore_open()
# 1001 AND 1002 both carry geometry, so both restore; 1003 (no SL/TP) must NOT.
print(f"restored from synthetic: {n3} (expected 2 — 1001 and 1002; NOT 1003)")
ok = ok and n3 == 2
print(f"1003 (no geometry) tracked? {m2.is_tracked(1003)} (expected False)")
ok = ok and not m2.is_tracked(1003)

dropped = m2.drop_missing({1001})
print(f"drop_missing(live={{1001}}) dropped: {dropped} (expected 2 -> 1002, 1003)")
ok = ok and dropped == 2

checks = [
    ("1001 still tracked", m2.is_tracked(1001), True),
    ("1002 phantom untracked", m2.is_tracked(1002), False),
    ("1002 reason", pm.trade_paths[1002].get("exit_reason"), "closed_while_down"),
    ("1004 untouched", pm.trade_paths[1004].get("exit_reason"), "stop_loss"),
    ("1001 kept its sample history",
     m2._limits[1001]["rec"].get("samples"), 3),
]
for name, got, want in checks:
    flag = "ok " if got == want else "BAD"
    print(f"  [{flag}] {name}: {got!r} (want {want!r})")
    ok = ok and got == want

# The restored position must still enforce: a profit at/below the stop must sell.
print("\n--- enforcement smoke test on the restored position ---")
lim = m2._limits[1001]
print(f"  stop level = {lim['stop']} (expect -0.2), take-profit = {lim['take_profit']}")
ok = ok and abs(lim["stop"] + 0.2) < 1e-9

if os.path.exists(tmp):
    os.remove(tmp)
print("\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
