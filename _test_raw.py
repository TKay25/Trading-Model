"""Raw diagnostic: send requests to Deriv and print every incoming message."""
import asyncio
import json
import sys
import websockets

WS_URL = "wss://ws.derivws.com/websockets/v3?app_id=1089"


async def main():
    print("connecting...", flush=True)
    ws = await websockets.connect(WS_URL, ping_interval=20, ping_timeout=20)
    print("connected", flush=True)

    # First, the simplest possible request: server time (no auth, no args)
    print("sending 'time'...", flush=True)
    await ws.send(json.dumps({"time": 1, "req_id": "t1"}))
    try:
        msg = await asyncio.wait_for(ws.recv(), timeout=10)
        print("time response:", msg[:300], flush=True)
    except asyncio.TimeoutError:
        print("time response: TIMEOUT", flush=True)

    # Then a ticks_history candles request
    print("sending ticks_history candles...", flush=True)
    await ws.send(json.dumps({
        "ticks_history": "R_100",
        "adjust_start_time": 1,
        "count": 10,
        "end": "latest",
        "style": "candles",
        "granularity": 60,
        "req_id": "c1",
    }))
    try:
        msg = await asyncio.wait_for(ws.recv(), timeout=10)
        print("candles response:", msg[:500], flush=True)
    except asyncio.TimeoutError:
        print("candles response: TIMEOUT", flush=True)

    await ws.close()


asyncio.run(main())
