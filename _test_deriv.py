"""Quick manual test for the fixed Deriv client (not part of the app)."""
import asyncio
import json
import logging
import sys

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

sys.path.insert(0, r"c:/Users/tzvakasikwa/OneDrive - CBZ Bank Limited/Documents/GitHub/Trading Model")
from deriv_api import DerivAPI


async def main():
    api = DerivAPI(app_id="1089", api_token="")
    ok = await api.connect()
    print("connected:", ok, flush=True)
    if not ok:
        return

    try:
        result = await api.get_candles("R_100", 60, 50)
        if "candles" in result:
            cs = result["candles"]
            print("candles received:", len(cs), flush=True)
            if cs:
                print("first candle:", json.dumps(cs[0]), flush=True)
                print("last candle:", json.dumps(cs[-1]), flush=True)
        else:
            print("ERROR response:", json.dumps(result)[:600], flush=True)
    finally:
        await api.close()


asyncio.run(main())
