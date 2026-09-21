"""Live check: are the positions open at Deriv actually being tracked/watched?

Protection only exists if a contract is in position_monitor._limits, which is
what /api/paths reports as records with exit_reason == null.
"""
import json
import urllib.request


def get(path, timeout=45):
    with urllib.request.urlopen("http://127.0.0.1:5000" + path, timeout=timeout) as r:
        return json.load(r)


def cids(rows):
    out = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        c = r.get("contract_id") or r.get("cid") or r.get("id")
        if c:
            out[int(c)] = r
    return out


def main():
    raw = {p: get(p) for p in ("/api/auto/status", "/api/paths", "/api/positions")}
    for p, v in raw.items():
        if isinstance(v, dict):
            print(f"{p}: dict keys={list(v)[:15]}")
        else:
            print(f"{p}: {type(v).__name__} len={len(v)}")
    print()

    st = raw["/api/auto/status"]
    if isinstance(st, dict) and isinstance(st.get("config"), dict):
        st = st["config"]
    print("--- auto/trader config ---")
    for k in ("enabled", "paper", "min_strength", "strict_flip", "stop_mode",
              "sl_atr_k", "tp_atr_k", "stop_loss_pct", "take_profit_pct",
              "risk_by_tf", "profit_target"):
        print(f"  {k}: {st.get(k)}")
    print("  qualifying:", st.get("qualifying"))
    print("  refused:", json.dumps(st.get("refused") or {}))

    pos_raw = raw["/api/positions"]
    pos = pos_raw
    if isinstance(pos_raw, dict):
        for k in ("positions", "contracts", "open"):
            if isinstance(pos_raw.get(k), list):
                pos = pos_raw[k]
                break
    pos = pos if isinstance(pos, list) else []
    live = cids(pos)
    print(f"\n--- open positions at Deriv: {len(live)} ---")

    pa = raw["/api/paths"]
    print("\n--- path counters ---")
    if isinstance(pa, dict):
        for k in ("total", "open", "closed", "stop_loss_exits", "take_profit_exits",
                  "settled_exits", "dipped_then_recovered", "avg_mae_pct", "avg_mfe_pct"):
            print(f"  {k}: {pa.get(k)}")
    recs = pa.get("records") if isinstance(pa, dict) else pa
    recs = recs or []
    if isinstance(recs, dict):
        recs = list(recs.values())
    watch = {}
    for r in recs:
        if not isinstance(r, dict):
            continue
        r.setdefault("cid", r.get("contract_id"))
        if r.get("exit_reason") is None and r.get("cid") is not None:
            watch[int(r["cid"])] = r
    print(f"--- positions the monitor is watching: {len(watch)} ---")

    missing = sorted(set(live) - set(watch))
    print(f"\n*** OPEN BUT NOT WATCHED (no stop, no target): {len(missing)} ***")
    for c in missing:
        r = live[c]
        print(f"  {c} {r.get('underlying_symbol') or r.get('symbol')} "
              f"{r.get('contract_type')} buy={r.get('buy_price')} "
              f"mult={r.get('multiplier')}")

    extra = sorted(set(watch) - set(live))
    if extra:
        print(f"\nwatched but not open at Deriv: {len(extra)} -> {extra[:10]}")

    if watch:
        print("\n--- sampled counts of watched positions ---")
        for c, r in sorted(watch.items()):
            print(f"  {c} {r.get('symbol')} {r.get('timeframe')} "
                  f"samples={r.get('samples')} sl={r.get('stop_loss')} "
                  f"min={r.get('profit_min')} last={r.get('profit_last')}")

    print("\n--- path summary ---")
    print(json.dumps({k: v for k, v in (pa.items() if isinstance(pa, dict) else [])
                      if k != "records"}, indent=1))


if __name__ == "__main__":
    main()
