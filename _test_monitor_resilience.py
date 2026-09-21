"""Prove the position monitor can no longer be killed by a network failure.

The 2026-09-21 outage: the LAN->WiFi switch broke the authenticated session, the
monitor's reconnect raised, and the exception escaped `_loop` to `_run`, which
logged one line and ended the thread FOR GOOD. `last_ok` then sat at 16:29 for
2h40m while `rate_limited=0` and `throttled=False` — every health flag read clean
with stop-loss enforcement entirely dead.

This drives the REAL `_loop` against an API whose connect() always fails, and
asserts three things:
  1. `_loop` does NOT escape — it keeps running instead of killing the thread.
  2. it KEEPS RETRYING (more than one connect attempt).
  3. the stall guard does NOT touch `last_ok` — that field must keep meaning
     "the last time Deriv actually answered", or the guard would hide the very
     stall it exists to expose.
"""
import asyncio
import time

import position_monitor as pm

ATTEMPTS = {"n": 0}


class BoomAPI:
    """Every connect fails, as it did when the network changed."""

    def __init__(self, app_id=None, api_token=None, account_type=None):
        pass

    async def connect(self, **kw):
        ATTEMPTS["n"] += 1
        raise ConnectionError("simulated: network down during reconnect")

    async def close(self):
        pass


pm.DerivAPI = BoomAPI          # patch the name `_loop` resolves at call time

m = pm.PositionMonitor()
m._running = True
m._reconciled = True
m.track(7777, "R_25", 1.00, 2.00, break_even=True, break_even_pct=0.0,
        meta={"stake": 1.0})

STALE = time.time() - 200.0
pm._live_health["last_ok"] = STALE


async def run():
    try:
        await asyncio.wait_for(m._loop(), timeout=9)
        return "RETURNED"
    except asyncio.TimeoutError:
        return "STILL-RUNNING"


outcome = asyncio.run(run())
last_ok_after = pm._live_health.get("last_ok")
kick = pm._live_health.get("last_kick")

print(f"  connect attempts while failing : {ATTEMPTS['n']}")
print(f"  _loop outcome after 9s         : {outcome}")
print(f"  last_ok preserved (not re-armed): {abs(last_ok_after - STALE) < 1e-6}")
print(f"  stall guard fired (last_kick)   : {kick > 0}")

ok = (outcome == "STILL-RUNNING" and ATTEMPTS["n"] >= 2
      and abs(last_ok_after - STALE) < 1e-6 and kick > 0)
print("\nRESULT:", "PASS — a failing connect can no longer kill position monitoring"
      if ok else "FAIL")
