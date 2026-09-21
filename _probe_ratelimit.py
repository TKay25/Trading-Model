"""Measure the proposal_open_contract rate limit so we can size the fix.

Fires a burst of POC calls back-to-back and reports which ones Deriv accepts,
then repeats after a quiet period to see how the budget refills.

RUN WITH THE BOT STOPPED, otherwise the app's own polling pollutes the result.
"""
import asyncio
import json
import time

from config import Config
from deriv_api import DerivAPI

BURST = 14


async def main():
    api = DerivAPI(app_id=Config.DERIV_APP_ID,
                   api_token=Config.DERIV_API_TOKEN,
                   account_type=Config.DERIV_ACCOUNT_TYPE)
    await api.connect(authenticated=True)

    pf = await api.get_portfolio()
    contracts = (pf.get("portfolio") or {}).get("contracts") or []
    ids = [int(c["contract_id"]) for c in contracts]
    print("OPEN_CONTRACTS:", len(ids), flush=True)
    if not ids:
        await api.close()
        return

    async def burst(label):
        t0 = time.time()
        ok = 0
        first_fail = None
        gaps = []
        last_ok = None
        for i in range(BURST):
            cid = ids[i % len(ids)]
            try:
                r = await api._send_request(
                    {"proposal_open_contract": 1, "contract_id": cid}, timeout=15)
            except Exception as e:
                print(f"  {i}: RAISED {e!r}", flush=True)
                continue
            code = ((r or {}).get("error") or {}).get("code")
            if code is None:
                ok += 1
                if last_ok is not None:
                    gaps.append(round(time.time() - last_ok, 3))
                last_ok = time.time()
            else:
                if first_fail is None:
                    first_fail = i
                    print(f"  first rejection at call #{i} "
                          f"({round(time.time() - t0, 2)}s in): {code}", flush=True)
        print(f"{label}: accepted {ok}/{BURST}, first_reject_idx={first_fail}, "
              f"elapsed {round(time.time() - t0, 2)}s, "
              f"success_gaps={gaps}", flush=True)

    await burst("BURST1")
    print("sleeping 8s ...", flush=True)
    await asyncio.sleep(8)
    await burst("BURST2 (after 8s quiet)")
    print("sleeping 20s ...", flush=True)
    await asyncio.sleep(20)
    await burst("BURST3 (after 20s quiet)")

    await api.close()


if __name__ == "__main__":
    asyncio.run(main())
