"""List every conflict that build_geofence_release.validate_fixes_file
would surface in a fixes CSV, instead of failing at the first one.

Same rules as ``validate_fixes_file``:
  * literal duplicates
  * allow rows covered by a block row on the same taxon or any ancestor
    in the same country at a wider-or-equal scope
  * (taxon, rule, USA) groups covering all 50 states

Usage:
    python list_fixes_conflicts.py <fixes.csv>
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from paths import CAMERATRAPAI_DIR  # noqa: E402

US_STATE_CODES = frozenset({
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
})


def is_descendant(child: str, parent: str) -> bool:
    cp = child.split(";")
    pp = parent.split(";")
    if len(cp) != 5 or len(pp) != 5:
        return False
    pd_ = 0
    for p in pp:
        if p:
            pd_ += 1
        else:
            break
    cd = 0
    for p in cp:
        if p:
            cd += 1
        else:
            break
    if cd <= pd_:
        return False
    return cp[:pd_] == pp[:pd_]


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 \
        else CAMERATRAPAI_DIR / "data" / "geofence_fixes.csv"

    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))

    rules: list[tuple[int, str, str, str, str]] = []
    saw_header = False
    for i, row in enumerate(rows, start=1):
        if not row or all(c == "" for c in row):
            continue
        if row[0].startswith("#"):
            continue
        if not saw_header:
            saw_header = True
            continue
        if len(row) < 4:
            continue
        rules.append((i, row[0].lower(), row[1].lower(), row[2], row[3]))

    # Duplicates
    print(f"== Literal duplicates ==")
    seen: dict[tuple, list[int]] = defaultdict(list)
    for line, lab, rt, c, s in rules:
        seen[(lab, rt, c, s)].append(line)
    dup_count = 0
    for key, lines in seen.items():
        if len(lines) > 1:
            dup_count += 1
            print(f"  {key}: lines {lines}")
    print(f"Total duplicate rules: {dup_count}\n")

    # Conflicts
    print(f"== Parent/self block covers allow ==")
    blocks = [(l, t, c, s) for l, t, r, c, s in rules if r == "block"]
    allows = [(l, t, c, s) for l, t, r, c, s in rules if r == "allow"]
    conflicts = 0
    for a_line, a_t, a_c, a_s in allows:
        for b_line, b_t, b_c, b_s in blocks:
            if a_c != b_c:
                continue
            if a_t != b_t and not is_descendant(a_t, b_t):
                continue
            if b_s == "" or b_s == a_s:
                conflicts += 1
                print(f"  allow L{a_line} ({a_t}, {a_c}, {a_s}) "
                      f"covered by block L{b_line} ({b_t}, {b_c}, {b_s})")
    print(f"Total conflicts: {conflicts}\n")

    # All-50 patterns
    print(f"== Rules enumerated across all 50 US states ==")
    by_group: dict[tuple[str, str], set[str]] = defaultdict(set)
    first_line: dict[tuple[str, str], int] = {}
    for line, lab, rt, c, s in rules:
        if c != "USA" or not s:
            continue
        by_group[(lab, rt)].add(s)
        first_line.setdefault((lab, rt), line)
    all_50 = 0
    for group, states in by_group.items():
        if US_STATE_CODES.issubset(states):
            all_50 += 1
            print(f"  {group}: starts near line {first_line[group]}")
    print(f"Total: {all_50}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
