"""Diagnose why the position monitor cannot read profit for open positions.

The monitor calls `proposal_open_contract` per tracked contract and treats a
missing `profit` as "no data". When EVERY contract returns no data it assumes
the link is broken and reconnects -- which is what is happening in a loop.

This script talks to Deriv directly and prints the RAW payloads so we can see
whether the problem is the request, the contract ids, the session, or a limit.
"""
import asyncio
import json
import sys

from config import Config
from deriv_api import DerivAPI


async def main():
    api = DerivAPI(app_id=Config.DERIV_APP_ID,
                   api_token=Config.DERIV_API_TOKEN,
                   account_type=Config.DERIV_ACCOUNT_TYPE)
    await api.connect(authenticated=True)

    bal = await api.get_balance()
    print("BALANCE:", json.dumps(bal)[:300])

    pf = await api.get_portfolio()
    print("PORTFOLIO_RAW:", json.dumps(pf)[:1800])

    contracts = (pf.get("portfolio") or {}).get("contracts") or []
    ids = [c.get("contract_id") for c in contracts if c.get("contract_id")]
    print("PORTFOLIO_COUNT:", len(ids))

    for cid in ids[:3]:
        try:
            resp = await api._send_request({"proposal_open_contract": 1,
                                            "contract_id": int(cid)})
        except Exception as e:
            print(f"POC {cid} RAISED {e!r}")
            continue
        print(f"POC {cid} RAW:", json.dumps(resp)[:900])

    # Also try the subscription form Deriv documents for the newest contract.
    if ids:
        try:
            resp = await api._send_request({"proposal_open_contract": 1,
                                            "contract_id": int(ids[0]),
                                            "subscribe": 1})
            print("POC_SUB RAW:", json.dumps(resp)[:900])
        except Exception as e:
            print("POC_SUB RAISED", repr(e))

    await api.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print("FATAL:", repr(exc))
        sys.exit(1)
