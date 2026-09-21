"""Probe the REAL contract_update shape for multiplier positions.

WHY THIS EXISTS
---------------
The POC payload reports `is_valid_to_update: {stop_loss: 1, take_profit: 1}` on
open MULTUP/MULTDOWN contracts, which means Deriv supports limit orders on
multipliers. If we set them at buy time, stop enforcement stops depending on a
2s polling loop (and on the proposal_open_contract quota, restarts, and thread
health) and becomes the exchange's own responsibility.

But the shape is not guessable and it moves real money, so this script:
  1. prints the RAW payload (limit_order / is_valid_to_update) for a position,
  2. tries the legacy `contract_update` envelope and, if rejected, a couple of
     alternatives, printing Deriv's exact error text for each,
  3. re-reads the contract to prove whether the limit actually took effect.

SAFETY: the stop/target are derived from the position's CURRENT profit (a fixed
distance either side of where it is right now), so the test cannot retroactively
close an existing position the way a re-derived stop did before.
"""
import asyncio
import json

from config import Config
from deriv_api import DerivAPI

# Put the test band this many dollars either side of the current P/L.
BAND = 3.0


def show(tag, resp):
    print(f"\n--- {tag} ---")
    if isinstance(resp, dict) and resp.get("error"):
        print(json.dumps(resp["error"], indent=1))
    else:
        print(json.dumps(resp, indent=1)[:1500])
    return resp


async def main():
    api = DerivAPI(app_id=Config.DERIV_APP_ID,
                   api_token=Config.DERIV_API_TOKEN,
                   account_type=Config.DERIV_ACCOUNT_TYPE)
    await api.connect(authenticated=True)
    print("AUTH OK, balance =", (await api.get_balance()).get("balance"))

    pf = await api.get_portfolio()
    contracts = (pf.get("portfolio") or {}).get("contracts") or []
    print("OPEN CONTRACTS:", len(contracts))
    if not contracts:
        print("Nothing open - cannot probe. Try again when a position is open.")
        await api.close()
        return

    target = contracts[0]
    cid = int(target["contract_id"])
    print("TARGET", cid, target.get("underlying_symbol"), target.get("contract_type"),
          "profit=", target.get("profit"))

    # 1. RAW payload - the first POC call on a fresh session always answers.
    poc = await api._send_request({"proposal_open_contract": 1, "contract_id": cid})
    await asyncio.sleep(0.4)
    c = (poc or {}).get("proposal_open_contract") or {}
    print("\nPOC RAW:", json.dumps(poc, indent=1)[:2200])
    print("\nKEYS:", sorted(c.keys()))
    print("limit_order      :", json.dumps(c.get("limit_order")))
    print("is_valid_to_update:", json.dumps(c.get("is_valid_to_update")))
    print("multiplier       :", c.get("multiplier"), " buy_price:", c.get("buy_price"))

    profit = c.get("profit")
    if profit is None:
        print("No profit in payload - aborting before attempting any update.")
        await api.close()
        return

    valid = c.get("is_valid_to_update") or {}
    if not valid.get("stop_loss") and not valid.get("take_profit"):
        print("Deriv says this contract is NOT valid to update - nothing to wire.")
        await api.close()
        return

    sl = float(profit) - BAND      # stop  $BAND worse than where we are now
    tp = float(profit) + BAND      # target $BAND better
    print(f"\nWILL TRY: stop_loss={sl:.2f} take_profit={tp:.2f} (profit now {profit})")

    # 2. Candidate envelopes, legacy first.
    attempts = [
        ("legacy: contract_update=1 + limit_order",
         {"contract_update": 1, "contract_id": cid,
          "limit_order": {"stop_loss": sl, "take_profit": tp}}),
        ("object form: contract_update={contract_id, limit_order}",
         {"contract_update": {"contract_id": cid,
                              "limit_order": {"stop_loss": sl, "take_profit": tp}}}),
        ("flat: contract_update=1 + stop_loss/take_profit",
         {"contract_update": 1, "contract_id": cid,
          "stop_loss": sl, "take_profit": tp}),
    ]

    ok = None
    for label, req in attempts:
        try:
            resp = await api._send_request(req)
        except Exception as exc:
            print(f"\n--- {label} --- RAISED {exc!r}")
            continue
        show(label, resp)
        if isinstance(resp, dict) and not resp.get("error"):
            ok = label
            print(">>> ACCEPTED:", label)
            break
        await asyncio.sleep(0.6)

    if not ok:
        print("\nNo envelope accepted. Deriv's error text above is the authority; "
              "send it to me and I will match the shape exactly.")
        await api.close()
        return

    # 3. Did it stick? Re-read after a moment.
    await asyncio.sleep(1.5)
    poc2 = await api._send_request({"proposal_open_contract": 1, "contract_id": cid})
    c2 = (poc2 or {}).get("proposal_open_contract") or {}
    print("\nAFTER RE-READ limit_order:", json.dumps(c2.get("limit_order")))
    print("AFTER RE-READ profit     :", c2.get("profit"))

    # 4. Clean up: clear the test limits so we do not leave a stray stop on it.
    clear = {"contract_update": 1, "contract_id": cid,
             "limit_order": {"stop_loss": 0, "take_profit": 0}}
    if ok != "legacy: contract_update=1 + limit_order":
        clear = {"contract_update": {"contract_id": cid,
                                     "limit_order": {"stop_loss": 0, "take_profit": 0}}}
    await asyncio.sleep(0.6)
    show("CLEAR (set both to 0)", await api._send_request(clear))

    await api.close()
    print("\nDONE")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print("FATAL:", repr(exc))
