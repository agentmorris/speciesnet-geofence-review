"""Detect whether the `_merge_country_rule_lists` bug in
propagate_rules would affect any taxa given a particular fixes CSV.

The bug: when two block-rule sources are merged for the same country,
the merge silently loses "[]" (country-wide) semantics if it gets
combined with a non-empty state list.  In particular:

  source.C = []     + target.C = ['AZ']   ->  buggy result ['AZ']
                                             correct result []
  source.C = ['AZ'] + target.C = []       ->  buggy result ['AZ']
                                             correct result []

For the bug to fire we need TWO OR MORE distinct contributions for
the same (label, country) with mixed empty/non-empty semantics.  The
contributions come from:

  (a) label's OWN block (from fix_geofence_base).
  (b) every ancestor of label that has its own block (propagated down).

This script builds the geofence dict the same way fix_geofence_base
does, enumerates all (label, country) pairs that get more than one
contribution, and flags every one where the contributions have mixed
semantics.

Usage:
    python check_merge_bug.py <fixes.csv>
"""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from paths import CAMERATRAPAI_DIR  # noqa: E402

BASE = CAMERATRAPAI_DIR / "data" / "geofence_base.json"


def _is_descendant(label: str, ancestor: str) -> bool:
    """True if `label` (5-token) is a strict descendant of `ancestor`
    (5-token, ancestor's trailing tokens are empty).  Matches the
    semantics the build script intends -- a prefix match on the
    non-empty tokens.
    """
    a = ancestor.rstrip(";").split(";")
    l = label.split(";")
    if len(l) != 5 or len(a) > 5 or len(a) == 0:
        return False
    if len(a) >= len([t for t in l if t]):
        return False
    return l[: len(a)] == a


def apply_fixes(geofence: dict, fixes_path: Path) -> dict:
    """Light-weight fix application that mirrors fix_geofence_base
    closely enough for this analysis.  No warnings, no widening
    correctness fixes -- just enough to compute the post-fix state."""
    with fixes_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        saw_header = False
        for row in reader:
            if not row or all(c == "" for c in row):
                continue
            if row[0].startswith("#"):
                continue
            if not saw_header:
                saw_header = True
                continue
            if len(row) < 4:
                continue
            label, rule, country, state = (
                row[0].lower(), row[1].lower(), row[2], row[3]
            )
            if rule == "allow":
                if label not in geofence:
                    continue
                if "allow" not in geofence[label]:
                    continue
                if not state:
                    geofence[label]["allow"][country] = []
                else:
                    cur = geofence[label]["allow"].get(country)
                    if cur is None:
                        geofence[label]["allow"][country] = [state]
                    elif not cur:
                        pass
                    else:
                        geofence[label]["allow"][country] = sorted(
                            set(cur) | {state}
                        )
            else:
                if label not in geofence:
                    geofence[label] = {
                        "block": {country: [state] if state else []}
                    }
                if "block" not in geofence[label]:
                    geofence[label]["block"] = {
                        country: [state] if state else []
                    }
                if not state:
                    geofence[label]["block"][country] = []
                else:
                    cur = geofence[label]["block"].get(country)
                    if cur is None:
                        geofence[label]["block"][country] = [state]
                    elif not cur:
                        pass
                    else:
                        geofence[label]["block"][country] = sorted(
                            set(cur) | {state}
                        )
    return geofence


def main() -> int:
    fixes_path = Path(sys.argv[1])
    geofence = json.loads(BASE.read_text(encoding="utf-8"))
    geofence = apply_fixes(geofence, fixes_path)

    # Find all labels with block rules, indexed by 5-token taxon string.
    blockers = {
        label: rule["block"]
        for label, rule in geofence.items()
        if "block" in rule and rule["block"]
    }

    # Build "contributions[label][country]" -> list of state lists.  A
    # contribution is either label's own block.country or an ancestor's
    # block.country (which would propagate down to this label).
    all_labels = set(geofence.keys()) | set(blockers.keys())
    contributions: dict[str, dict[str, list[list[str]]]] = (
        defaultdict(lambda: defaultdict(list))
    )

    for label in all_labels:
        # Own block.
        own_block = geofence.get(label, {}).get("block") or {}
        for country, states in own_block.items():
            contributions[label][country].append(states)

        # Block from each ancestor.
        for ancestor, blk in blockers.items():
            if ancestor == label:
                continue
            if not _is_descendant(label, ancestor):
                continue
            for country, states in blk.items():
                contributions[label][country].append(states)

    # Find (label, country) where the bug would fire: 2+ contributions,
    # mixed empty / non-empty.
    hits: list[tuple[str, str, list[list[str]]]] = []
    for label, by_country in contributions.items():
        for country, srcs in by_country.items():
            if len(srcs) < 2:
                continue
            has_empty = any(len(s) == 0 for s in srcs)
            has_nonempty = any(len(s) > 0 for s in srcs)
            if has_empty and has_nonempty:
                hits.append((label, country, srcs))

    print(f"Pairs (label, country) where the merge bug would fire: {len(hits)}")
    for label, country, srcs in hits:
        print(f"  {label}  country={country}")
        for s in srcs:
            shape = "[] (country-wide)" if not s else f"states={s}"
            print(f"      contribution: {shape}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
