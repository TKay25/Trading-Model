"""Check current .env token structure + test against Deriv (no token printed)."""
import asyncio
import json
import os
import sys
from dotenv import load_dotenv

sys.path.insert(0, r"c:/Users/tzvakasikwa/OneDrive - CBZ Bank Limited/Documents/GitHub/Trading Model")
load_dotenv(r"c:/Users/tzvakasikwa/OneDrive - CBZ Bank Limited/Documents/GitHub/Trading Model/.env", override=True)
from deriv_api import DerivAPI


async def main():
    token = os.getenv("DERIV_API_TOKEN", "")
    print("token present:", bool(token), "| length:", len(token), flush=True)
    print("first2/last2:", (token[:2] + "..." + token[-2:]) if token else "-", flush=True)
    bad = [(i, repr(ch)) for i, ch in enumerate(token) if not ch.isalnum()]
    print("non-alphanumeric positions:", bad if bad else "none (clean)", flush=True)

    api = DerivAPI(app_id="1089", api_token=token)
    ok = await api.connect()
    print("websocket connect (app_id=1089):", ok, flush=True)
    if ok:
        resp = await api._send_request({"authorize": token})
        if "error" in resp:
            print("authorize: FAILED ->", json.dumps(resp["error"]), flush=True)
        else:
            print("authorize: OK ->", resp.get("authorize", {}).get("loginid"), flush=True)
        await api.close()


asyncio.run(main())
