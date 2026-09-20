"""TEMP: how far back does proposal_open_contract reach? (backfill feasibility)"""
import asyncio

from config import Config
from deriv_api import DerivAPI

CIDS = [10262434619, 10264401699, 11572138619, 11664277459, 11715275979]


async def main():
    api = DerivAPI(Config.DERIV_APP_ID, Config.DERIV_API_TOKEN, Config.DERIV_ACCOUNT_TYPE)
    await api.connect(authenticated=True, timeout=60)
    try:
        for cid in CIDS:
            try:
                resp = await api._send_request({"proposal_open_contract": 1, "contract_id": int(cid)})
                if "error" in resp:
                    print(f"cid={cid} ERROR_MSG: {(resp.get('error') or {}).get('message')}", flush=True)
                    continue
                poc = resp.get("proposal_open_contract") or {}
                keys = {k: poc.get(k) for k in ("contract_id", "status", "profit", "sell_price", "buy_price") if k in poc}
                print(f"cid={cid} -> {keys}", flush=True)
            except Exception as e:
                print(f"cid={cid} EXC {e!r}", flush=True)
    finally:
        try:
            await api.close()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"PROBE FAILED: {e!r}")
        import traceback
        traceback.print_exc()
