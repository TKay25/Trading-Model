"""Boot/health check for the running BotTraderX5 server."""
import json
import os
import time
import urllib.request

BASE = "http://127.0.0.1:5000"
HERE = os.path.dirname(os.path.abspath(__file__))

# A corporate HTTP proxy (WinINET/registry) intercepts loopback requests from
# urllib and answers 502 "Operation not permitted", while the browser bypasses
# the proxy for localhost. Build a proxy-free opener for all local calls.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(path, timeout=60):
    with _OPENER.open(BASE + path, timeout=timeout) as r:
        return json.load(r)


def main():
    print("proxies seen by urllib:", urllib.request.getproxies() or "{}")
    s = get("/api/auto/status")
    c = s.get("config") or {}
    print("--- config ---")
    for k in ("enabled", "paper", "profit_target", "profit_target_mode",
              "min_strength", "stop_mode", "sl_atr_k", "tp_atr_k", "strict_flip",
              "learning"):
        print(f"  {k}: {c.get(k)}")
    print(f"  risk_by_tf: {c.get('risk_by_tf')}")

    at = s.get("at")
    print(f"\nlast cycle: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(at)) if at else None}"
          f"  (scan {s.get('scan')})")
    print(f"message: {s.get('message')}")
    print(f"signals: {s.get('signals')}  qualifying: {s.get('qualifying')}  "
          f"refused: {s.get('refused')}")

    p = get("/api/paths")
    print("\n--- path counters ---")
    for k in ("total", "open", "closed", "stop_loss_exits", "take_profit_exits",
              "settled_exits", "dipped_then_recovered", "avg_mae_pct", "avg_mfe_pct",
              "avg_hold_s"):
        print(f"  {k}: {p.get(k)}")

    try:
        pos = get("/api/positions").get("positions") or []
        net = sum(float(x.get("profit") or 0) for x in pos)
        print(f"\nopen positions: {len(pos)}   floating net P/L: ${net:+.2f}  "
              f"(close-all triggers at +$8.00)")
        from collections import Counter
        print("  by symbol:", dict(Counter(x.get("symbol") or x.get("underlying_symbol")
                                          for x in pos)))
    except Exception as e:
        print("positions error:", e)

    # did the $8 rule ever fire? look for profit_target exits in the ledger
    try:
        led = json.load(open(os.path.join(HERE, "trade_results.json"), encoding="utf-8"))
        rows = [v for v in led.values() if isinstance(v, dict)] if isinstance(led, dict) else led
        print(f"\nresults ledger entries: {len(rows)}")
        from collections import Counter
        print("  statuses:", dict(Counter(r.get("status") for r in rows).most_common(6)))
        cut = time.time() - 12 * 3600
        recent = [r for r in rows if isinstance(r.get("close_time"), (int, float))
                  and r["close_time"] >= cut]
        print(f"  closed in the last 12h: {len(recent)}")
        if recent:
            burst = Counter(round(r["close_time"] / 30) * 30 for r in recent)
            top = burst.most_common(1)[0]
            print(f"  biggest 30s close-burst: {top[1]} contracts at "
                  f"{time.strftime('%H:%M:%S', time.localtime(top[0]))}")
    except Exception as e:
        print("ledger error:", e)


if __name__ == "__main__":
    main()
