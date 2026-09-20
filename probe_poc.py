"""TEMP: check proposal_open_contract shape on settled + open contracts."""
import asyncio

from config import Config
from deriv_api import DerivAPI

# contract ids observed in today's logs / profit_table
CIDS = [11771337439, 11771528319, 11771339499, 11771527279, 11775620679]


async def main():
    api = DerivAPI(Config.DERIV_APP_ID, Config.DERIV_API_TOKEN, Config.DERIV_ACCOUNT_TYPE)
    await api.connect(authenticated=True, timeout=60)
    try:
        # get current open portfolio first
        port = await api.get_portfolio()
        open_cids = set()
        p = port.get("portfolio", {}) if isinstance(port, dict) else {}
        for c in p.get("contracts", []):
            open_cids.add(c.get("contract_id"))
        print(f"currently open: {sorted(open_cids)}", flush=True)
        for cid in CIDS + sorted(open_cids)[:3]:
            try:
                resp = await api._send_request({"proposal_open_contract": 1, "contract_id": int(cid)})
                poc = resp.get("proposal_open_contract") or resp
                keys = {k: poc.get(k) for k in ("contract_id", "status", "profit", "sell_price", "buy_price", "payout") if k in poc}
                print(f"cid={cid} in_open={cid in open_cids} -> {keys}", flush=True)
            except Exception as e:
                print(f"cid={cid} ERROR {e!r}", flush=True)
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
