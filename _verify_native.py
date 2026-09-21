"""Independent read-back: is the stop really sitting ON the contracts?

The bot's log says it set them, but the log is our own claim derived from a
response. This opens a SEPARATE session and reads `limit_order` straight back
from Deriv for a handful of positions, then prints what the app is serving so
the config change (native_sl_tp, 30m disabled, learner state) is confirmed from
the running process rather than from the file on disk.

Local API calls go through a proxy-bypassing opener: this machine's corporate
proxy (10.10.4.5:80) intercepts even 127.0.0.1 and returns 502.
"""
import asyncio
import json
import urllib.request

from config import Config
from deriv_api import DerivAPI

LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def local(path):
    try:
        with LOCAL.open(f"http://127.0.0.1:5000{path}", timeout=20) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"_err": repr(e)}


async def main():
    api = DerivAPI(app_id=Config.DERIV_APP_ID,
                   api_token=Config.DERIV_API_TOKEN,
                   account_type=Config.DERIV_ACCOUNT_TYPE)
    await api.connect(authenticated=True)

    pf = await api.get_portfolio()
    contracts = (pf.get("portfolio") or {}).get("contracts") or []
    print(f"open contracts: {len(contracts)}")

    with_native, without, rows = 0, 0, []
    for c in contracts[:6]:
        cid = int(c["contract_id"])
        poc = await api._send_request({"proposal_open_contract": 1, "contract_id": cid})
        await asyncio.sleep(0.35)
        cc = (poc or {}).get("proposal_open_contract") or {}
        lo = cc.get("limit_order") or {}
        sl = (lo.get("stop_loss") or {}).get("order_amount")
        tp = (lo.get("take_profit") or {}).get("order_amount")
        rows.append((cid, cc.get("underlying_symbol"), cc.get("profit"), sl, tp))
        if sl is not None or tp is not None:
            with_native += 1
        else:
            without += 1
        print(f"  {cid} {cc.get('underlying_symbol'):>8} profit={cc.get('profit'):>6} "
              f"native stop_loss={sl} take_profit={tp}")

    # Count over the WHOLE book from the portfolio's own limit fields if present.
    print(f"\nsampled {len(rows)}: {with_native} have native limits, {without} do not")

    st = local("/api/auto/status")
    cfg = st.get("config") or {}
    print("\n--- what the APP is serving ---")
    print("  native_sl_tp      :", cfg.get("native_sl_tp"))
    print("  risk_by_tf        :", json.dumps(cfg.get("risk_by_tf")))
    print("  sl_atr_k/tp_atr_k :", cfg.get("sl_atr_k"), "/", cfg.get("tp_atr_k"))
    print("  scan              :", st.get("scan"), " qualifying:", st.get("qualifying"))

    sg = local("/api/strategy")
    print("  learner applied   :", json.dumps(sg.get("applied")))
    print("  learner records   :", sg.get("records"))
    print("  learner state     :", json.dumps(sg.get("state")))

    pa = local("/api/paths")
    lh = pa.get("live_health") or {}
    print("\n  paths: open", pa.get("open"), "closed", pa.get("closed"),
          "| monitor live_health throttled:", lh.get("throttled"),
          "rate_limited:", lh.get("rate_limited"), "cached:", lh.get("cached"))

    await api.close()
    print("\nDONE")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print("FATAL:", repr(exc))
