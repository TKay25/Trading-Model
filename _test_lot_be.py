"""Prove the user's rule: max loss = the LOT, and the only adjustment is at
break-even — so a trade ends at -lot or at ~0.

Drives the REAL PositionMonitor._check with a fake API (same harness as
_test_trail.py), because the point is to test the actual enforcement path, not a
re-implementation of the rule.

Config under test (auto_trader_config.json): stake $1 -> stop_loss $1.00 (the
whole lot), break_even_pct 0 (= arm at break-even itself), trail OFF, TP $2.00.

The cases that matter:
  * profit 0.00 must NOT arm anything and must NOT sell. With break_even_pct = 0
    the threshold is break-even, so a naive `profit >= 0` would arm here and the
    very next check (`profit <= stop`, i.e. 0 <= 0) would close the position the
    instant it opened.
  * profit +0.01 arms the stop at 0.
  * a later dip to -0.01 must SELL (the "zero" branch, not a loss).
  * -0.50 against a $1.00 lot must NOT sell: the loss is allowed to run to the
    full lot, that is the whole point.
  * -1.01 must SELL (the "lot" branch).
  * the stop must never move DOWN.
"""
import asyncio

import position_monitor as pm

STAKE, TP = 1.00, 2.00          # stop_loss = the whole lot = $1.00


class FakeAPI:
    def __init__(self):
        self.profit = 0.0
        self.sells = []

    async def _send_request(self, req):
        if "proposal_open_contract" in req:
            return {"proposal_open_contract": {"status": "open",
                                               "profit": self.profit}}
        if "sell" in req:
            self.sells.append(req["sell"])
            return {"sell": {"contract_id": req["sell"]}}
        return {}


def run_case(label, sequence, expect_sold_at):
    """sequence: list of (profit, note). Returns the observed timeline."""
    print(f"\n=== {label} ===")
    m = pm.PositionMonitor()
    m._running = True              # _check() bails unless it thinks it's running
    api = FakeAPI()
    m.track(2001, "R_25", STAKE, TP,
            break_even=True, break_even_pct=0.0,   # arm AT break-even
            trail=False,
            meta={"stake": STAKE})
    tracked = dict(m._limits)

    prev_stop, sold_at, below = None, None, []
    for profit, note in sequence:
        api.profit = profit
        asyncio.run(m._check(api, tracked))
        lim = m._limits.get(2001)
        stop = lim["stop"] if lim else None
        closed = lim is None
        print(f"  profit {profit:+.2f}  ->  stop "
              f"{'(closed)' if closed else format(stop, '+.2f')}"
              f"{'   SOLD' if closed else ''}   {note}")
        if stop is not None and prev_stop is not None and stop < prev_stop - 1e-9:
            below.append(f"{prev_stop:+.2f} -> {stop:+.2f}")
        if closed and sold_at is None:
            sold_at = profit
        prev_stop = stop
    return {"sold_at": sold_at, "expect_sold_at": expect_sold_at,
            "moved_down": below, "sells": api.sells}


results = []

# 1. THE REGRESSION GUARD: sitting exactly at break-even must change nothing.
results.append(("flat at 0.00 does nothing",
                run_case("flat at 0.00 -> no arm, no sell",
                         [(0.0, "exactly break-even"),
                          (-0.30, "still allowed: lot is the cap"),
                          (0.0, "back to break-even")],
                         expect_sold_at=None)))

# 2. Arm at break-even, then a dip must close at ~0 (the "zero" branch).
results.append(("arm then dip -> zero branch",
                run_case("+0.01 arms, -0.01 closes (ZERO branch)",
                         [(0.01, "first positive tick"),
                          (-0.01, "dip -> must close at ~0")],
                         expect_sold_at=-0.01)))

# 3. A loser is allowed to run to the full lot (the "-lot" branch).
results.append(("rides to the lot",
                run_case("no profit first -> rides to -lot",
                         [(-0.20, "no sell"), (-0.50, "no sell"), (-0.99, "no sell"),
                          (-1.00, "at the lot -> sell")],
                         expect_sold_at=-1.00)))

# 4. Once armed, the stop must never move back down.
results.append(("stop never moves down",
                run_case("armed stop is not re-lowered",
                         [(0.05, "arm -> stop 0"), (-0.02, "closes at the zero branch")],
                         expect_sold_at=-0.02)))

print("\n================ SUMMARY ================")
ok = True
for label, r in results:
    good = (r["sold_at"] == r["expect_sold_at"]) and not r["moved_down"]
    ok = ok and good
    print(f"  {'PASS' if good else 'FAIL'}  {label}: sold at {r['sold_at']} "
          f"(expected {r['expect_sold_at']}), stop moved down: {r['moved_down'] or 'never'}")
print("\nRESULT:", "ALL PASS" if ok else "FAILURES PRESENT")
