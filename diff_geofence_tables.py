"""Diff two geofence tables produced by generate_geofence_table.py.

Reports rows where the `blocked` column differs and summarizes by:
  * direction (newly allowed vs newly blocked)
  * taxon (which species drove most of the change)
  * location (country / admin1 most often involved)

Writes:
  * diffs.csv      -- one row per (taxon, country, admin1) where they differ
  * summary text printed to stdout

Usage:
    python diff_geofence_tables.py <main-table.csv> <current-table.csv> <out-diffs.csv>
"""

from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path


def load(path: Path) -> dict[tuple[str, str, str], str]:
    table: dict[tuple[str, str, str], str] = {}
    with path.open("r", encoding="utf-8", newline="") as f:
        r = csv.reader(f)
        header = next(r)
        assert header == ["taxon_key", "country", "admin1", "blocked"], header
        for taxon, country, admin1, blocked in r:
            table[(taxon, country, admin1)] = blocked
    return table


def main() -> int:
    main_path = Path(sys.argv[1])
    cur_path  = Path(sys.argv[2])
    out_path  = Path(sys.argv[3])

    print(f"loading {main_path.name} ...")
    main_t = load(main_path)
    print(f"loading {cur_path.name} ...")
    cur_t  = load(cur_path)

    print(f"main rows:    {len(main_t)}")
    print(f"current rows: {len(cur_t)}")
    assert main_t.keys() == cur_t.keys(), (
        f"row-sets differ: only-in-main={len(main_t.keys()-cur_t.keys())}, "
        f"only-in-current={len(cur_t.keys()-main_t.keys())}"
    )

    diffs: list[tuple[str, str, str, str, str]] = []
    # (taxon, country, admin1, main_blocked, cur_blocked)
    for key, m in main_t.items():
        c = cur_t[key]
        if m != c:
            diffs.append((*key, m, c))

    print()
    print(f"differing rows: {len(diffs)}")
    if not diffs:
        return 0

    # Summaries.
    newly_blocked = sum(1 for *_, m, c in diffs if m == "0" and c == "1")
    newly_allowed = sum(1 for *_, m, c in diffs if m == "1" and c == "0")
    print(f"  newly blocked (allow -> block): {newly_blocked}")
    print(f"  newly allowed (block -> allow): {newly_allowed}")

    by_taxon = Counter(t for t, *_ in diffs)
    by_country = Counter(c for _, c, *_ in diffs)

    print()
    print("top 20 taxa by # differing rows:")
    for taxon, n in by_taxon.most_common(20):
        print(f"  {n:5d}  {taxon}")

    print()
    print("top 20 countries by # differing rows:")
    for country, n in by_country.most_common(20):
        print(f"  {n:5d}  {country}")

    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["taxon_key", "country", "admin1",
                    "main_blocked", "current_blocked"])
        for row in diffs:
            w.writerow(row)
    print()
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
