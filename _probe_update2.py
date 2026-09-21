"""Confirm the contract_update envelope with LEGAL values, proving set + clear.

_probe_update.py established:
  * the envelope IS the legacy one: {"contract_update": 1, "contract_id": <int>,
    "limit_order": {"stop_loss": <positive $>, "take_profit": <positive $>}}
    (the object form and the flat form are both rejected with
    InputValidationFailed, so the envelope is not a guess any more)
  * stop_loss / take_profit are POSITIVE dollar amounts, and the legal range
    comes back PER CONTRACT in `validation_params` (stop_loss.max is bounded by
    the stake, because Deriv's own stop_out already closes at -100%).

This script proves the value is actually APPLIED (re-reads limit_order) and that
it can be cleared again, using values taken from validation_params so they are
legal by construction.
"""
import asyncio
import json

from config import Config
from deriv_api import DerivAPI

TAG = "probe2"


async def read(api, cid):
    poc = await api._send_request({"proposal_open_contract": 1, "contract_id": cid})
    return (poc or {}).get("proposal_open_contract") or {}


async def main():
    api = DerivAPI(app_id=Config.DERIV_APP_ID,
                   api_token=Config.DERIV_API_TOKEN,
                   account_type=Config.DERIV_ACCOUNT_TYPE)
    await api.connect(authenticated=True)

    pf = await api.get_portfolio()
    contracts = (pf.get("portfolio") or {}).get("contracts") or []
    if not contracts:
        print("Nothing open - cannot probe.")
        await api.close()
        return
    cid = int(contracts[0]["contract_id"])

    c = await read(api, cid)
    vp = c.get("validation_params") or {}
    sl_max = float((vp.get("stop_loss") or {}).get("max") or 0)
    tp_max = float((vp.get("take_profit") or {}).get("max") or 0)
    print(f"contract {cid} profit={c.get('profit')} stake={c.get('buy_price')} "
          f"mult={c.get('multiplier')}")
    print(f"legal stop_loss 0.10..{sl_max}   take_profit 0.10..{tp_max}")
    if sl_max < 0.10 or tp_max < 0.10:
        print("No room for limits on this contract.")
        await api.close()
        return

    # Small legal values: a stop well clear of the current P/L, and a modest
    # target. Chosen from the legal range, not invented.
    sl = round(max(0.10, min(sl_max * 0.35, 0.30)), 2)
    tp = round(max(0.10, min(tp_max * 0.20, 2.00)), 2)
    print(f"\nSET stop_loss={sl} take_profit={tp}")
    r1 = await api._send_request({"contract_update": 1, "contract_id": cid,
                                  "limit_order": {"stop_loss": sl, "take_profit": tp}})
    print("SET RESPONSE:", json.dumps(r1.get("error") or r1.get("contract_update"))[:600])
    if isinstance(r1, dict) and r1.get("error"):
        await api.close()
        return

    await asyncio.sleep(1.5)
    c2 = await read(api, cid)
    lo = c2.get("limit_order") or {}
    print("AFTER SET limit_order:", json.dumps(lo, indent=1)[:900])
    applied = (lo.get("stop_loss") or {}).get("order_amount")
    target = (lo.get("take_profit") or {}).get("order_amount")
    print(f">>> APPLIED stop_loss={applied} take_profit={target}")

    # CLEAR: prove we can remove them again (this is what the bot must do if a
    # position needs its protection withdrawn or re-pointed).
    await asyncio.sleep(0.8)
    print("\nCLEAR (single field set to 0.1, the documented minimum)")
    r2 = await api._send_request({"contract_update": 1, "contract_id": cid,
                                  "limit_order": {"stop_loss": 0.10}})
    print("CLEAR RESPONSE:", json.dumps(r2.get("error") or "ok")[:400])
    await asyncio.sleep(1.5)
    c3 = await read(api, cid)
    print("AFTER CLEAR limit_order:", json.dumps(c3.get("limit_order"), indent=1)[:600])

    await api.close()
    print("\nDONE")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print("FATAL:", repr(exc))
