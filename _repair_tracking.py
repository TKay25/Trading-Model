"""Repair tracking state destroyed by the empty-portfolio bug (2026-09-21).

WHAT HAPPENED: on boot, `_restore_live` fetched the portfolio; Deriv answered an
OVER-QUOTA request with an EMPTY payload; the code treated that as authoritative
and called `drop_missing(set())`, which retired EVERY persisted record as
`closed_while_down` -- while the positions were still open at Deriv. Result: 19
real positions with no stop, no target and no tracking, and a dashboard that
happily reported a flat book.

WHAT THIS DOES: re-opens exactly the records whose contracts the RESULTS LEDGER
still reports as open (`status == "open"` in trade_results.json). That ledger is
independent evidence from a different code path, so it is the right authority
for "is this contract still open".

RUN WITH THE BOT STOPPED -- the live process holds its own copy of trade_paths in
memory and would overwrite the repair on its next flush.
"""
import io
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PATHS = os.path.join(HERE, "trade_paths.json")
RESULTS = os.path.join(HERE, "trade_results.json")


def load(name):
    with io.open(name, encoding="utf-8") as f:
        return json.load(f)


def main():
    results = load(RESULTS)
    open_ids = set()
    for k, v in results.items():
        if not isinstance(v, dict):
            continue
        if str(v.get("status") or "").lower() == "open":
            try:
                open_ids.add(int(v.get("cid", k)))
            except (TypeError, ValueError):
                continue
    print("LEDGER_REPORTS_OPEN:", len(open_ids))

    paths = load(PATHS)
    repaired, already_open, no_record, kept_closed = [], [], [], []
    for cid in sorted(open_ids):
        rec = paths.get(str(cid))
        if rec is None:
            no_record.append(cid)
            continue
        if not rec.get("t_close"):
            already_open.append(cid)
            continue
        # Wrongly retired while the contract is still open -> un-retire it and
        # leave the original stop/target geometry completely untouched.
        rec.pop("t_close", None)
        rec.pop("exit_reason", None)
        rec.pop("hold_s", None)
        rec.pop("profit_final", None)
        rec.pop("profit_final_pct", None)
        rec.pop("closed_while_down_at", None)
        repaired.append((cid, rec.get("symbol"), rec.get("stop_loss"),
                         rec.get("take_profit"), rec.get("adopted")))

    # Records that are closed in the ledger but still look open in the paths
    # dataset are NOT touched here -- that is what drop_missing is for.
    print("REPAIRED (un-retired):", len(repaired))
    for r in repaired:
        print("   cid=%s %s sl=%s tp=%s adopted=%s" % r)
    print("ALREADY_OPEN:", len(already_open))
    print("NO_PATH_RECORD (cannot restore):", len(no_record), sorted(no_record)[:20])
    print("KEPT_CLOSED:", len(kept_closed))

    tmp = PATHS + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        json.dump(paths, f)
    os.replace(tmp, PATHS)
    print("WROTE", PATHS)


if __name__ == "__main__":
    main()
