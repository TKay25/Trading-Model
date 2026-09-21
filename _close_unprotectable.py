"""Close the unprotectable R_50 positions (user-approved 2026-09-21).

They cannot be given a stop at any valid R_50 multiplier (80x floor), so they can
only end at Deriv's -100% close. Closes ONLY R_50 via /api/close_symbol.
"""
import json
import urllib.request

SYMBOL = "R_50"
BASE = "http://127.0.0.1:5000"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(path):
    with OPENER.open(BASE + path, timeout=90) as r:
        return json.load(r)


def post(path, payload):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(BASE + path, data=body,
                                 headers={"Content-Type": "application/json"})
    with OPENER.open(req, timeout=120) as r:
        return json.load(r)


def sym_of(p):
    return p.get("symbol") or p.get("underlying_symbol") or ""


def main():
    before = [p for p in (get("/api/positions").get("positions") or [])
              if sym_of(p) == SYMBOL]
    print(f"open {SYMBOL} positions BEFORE: {len(before)}")
    for p in before:
        print(f"  {p.get('contract_id')} {p.get('contract_type')} "
              f"stake={p.get('stake') or p.get('lot_size')} "
              f"mult={p.get('multiplier')} profit={p.get('profit')}")
    if not before:
        print("nothing to close")
        return

    print(f"\nPOST /api/close_symbol {{'symbol': '{SYMBOL}'}} ...")
    res = post("/api/close_symbol", {"symbol": SYMBOL})
    print("response:", json.dumps(res))

    after = [p for p in (get("/api/positions").get("positions") or [])
             if sym_of(p) == SYMBOL]
    print(f"\nopen {SYMBOL} positions AFTER: {len(after)}")
    for p in after:
        print(f"  STILL OPEN {p.get('contract_id')} profit={p.get('profit')}")

    total = len(get("/api/positions").get("positions") or [])
    print(f"total open positions now: {total}")


if __name__ == "__main__":
    main()
