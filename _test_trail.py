"""Does the stop RATCHET UP as a trade moves into profit?

Drives the REAL PositionMonitor._check with a fake API, so this tests the actual
enforcement path rather than a re-implementation of the formula.

Setup matches a live auto-trade: stake $1, SL $0.50 risk, TP $2.00 (R:R 1:4),
break-even at 25% of TP (= $0.50 profit), trail 25% of TP (= $0.50 behind the
best profit).
"""
import asyncio

import position_monitor as pm


class FakeAPI:
    """Answers proposal_open_contract with whatever profit we set."""

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


def drive(break_even, trail, label):
    print(f"\n=== {label} (break_even={break_even}, trail={trail}) ===")
    m = pm.PositionMonitor()
    # _check() bails out immediately unless the monitor believes it is running
    # (`if not self._running: return`), so set the flag WITHOUT starting the
    # background thread — we drive _check ourselves.
    m._running = True
    api = FakeAPI()
    m.track(1001, "R_10", 0.50, 2.00,
            break_even=break_even, trail=trail,
            break_even_pct=0.25, trail_pct=0.25,
            meta={"stake": 1.0})
    tracked = dict(m._limits)
    stops, prev = [], None

    for p in (0.10, 0.40, 0.60, 1.00, 1.20, 0.80, 0.30):
        api.profit = p
        asyncio.run(m._check(api, tracked))
        lim = m._limits.get(1001)
        stop = lim["stop"] if lim else None
        closed = lim is None
        print(f"  profit {p:+.2f}  ->  stop {stop if stop is None else format(stop, '+.2f')}"
              f"{'   SOLD (stop breached)' if closed else ''}")
        stops.append(stop)
        if stop is not None and prev is not None and stop < prev - 1e-9:
            print("    !! STOP MOVED DOWN — must never happen")
        prev = stop
    return stops


# --- with the ratchet enabled ------------------------------------------------
stops = drive(True, True, "BREAK-EVEN + TRAILING ENABLED")
armed = [s for s in stops if s is not None and s >= 0]
print(f"\n  stop reached break-even (>= 0): {bool(armed)}")
print(f"  highest stop reached: {max([s for s in stops if s is not None], default=None):+.2f}")
ratcheted = any(s is not None and s > 0 for s in stops)
print(f"  stop ended in PROFIT territory (> 0): {ratcheted}")

# --- the old behaviour, for contrast ----------------------------------------
stops_off = drive(False, False, "FIXED STOP (previous auto-trade behaviour)")
print(f"\n  highest stop reached: {max(stops_off):+.2f} (should stay -0.50)")

print("\nRESULT:", "PASS" if (armed and ratcheted and stops_off[0] == -0.5) else "CHECK ABOVE")
