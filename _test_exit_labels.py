"""Check the 'Closed By' labels that will appear in the history table."""
import collections

import position_monitor as pm

m = pm.exit_reason_map()
print(f"records with a label: {len(m)}")
print("\n--- label distribution ---")
for label, n in collections.Counter(m.values()).most_common():
    print(f"  {label:>28}: {n}")

# The exact contracts from the user's history table.
WANT = [13806746679, 13806544159, 13806542939, 13806441299, 13805532259,
        13802114399, 13801830719, 13801735239, 13801734479, 13801535119]
print("\n--- 'Closed By' for the rows the user pasted ---")
for c in WANT:
    print(f"  {c}: {m.get(c) or '(no path record -> column blank)'}")

# Grouping sanity: every closed reason must map to a label, never a raw token.
unknown = sorted({k for k, v in m.items() if isinstance(v, str) and v.islower()
                  and v not in pm._EXIT_LABELS and "(" not in v})
print(f"\nunmapped raw reasons still leaking through: {unknown or 'none'}")

stops = [k for k, v in m.items() if v.startswith("Stop-loss")]
arts = [k for k, v in m.items() if "adopted at boot" in v]
print(f"stop-loss labels: {len(stops)}   flagged as restart artefacts: {len(arts)}")
