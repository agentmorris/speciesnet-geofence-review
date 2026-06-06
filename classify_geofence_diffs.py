"""Bucket each [taxon, country, admin1] difference into one of:

  base_renamed     -- taxon exists in current geofence_base but not main
                      (or vice versa). Driven by the user's manual base edit.
  base_changed     -- taxon's geofence_base entry text differs between
                      branches.  Driven by manual base edit.
  csv_rule_added   -- there is a fixes-CSV rule on the current branch that
                      directly references the taxon and was not on main.
  csv_rule_ancestor -- an ancestor taxon (genus/family/...) has a fixes-CSV
                      rule added on the current branch but not on main.
  ancestor_allow_restored -- on main the taxon had no allow in the release
                      (suggests bug #8 stripped it), and our build keeps it.
  cw_widening      -- the taxon's allow.country is countrywide on current
                      and state-restricted on main (bug #1 fix).
  unexplained      -- none of the above.
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from collections import defaultdict, Counter
from pathlib import Path


CAMERATRAPAI = Path(r"C:/git/cameratrapai")
CURRENT_BASE = CAMERATRAPAI / "data/geofence_base.json"
CURRENT_FIXES = CAMERATRAPAI / "data/geofence_fixes.csv"
CURRENT_RELEASE = CAMERATRAPAI / "data/model_package/geofence_release.json"
MAIN_RELEASE = Path(r"G:/temp/speciesnet-geofence-review-data/main-geofence_release.json")
DIFFS_CSV = Path(r"G:/temp/speciesnet-geofence-review-data/diffs.csv")


def git_show(ref: str, path: str) -> str:
    cp = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        cwd=str(CAMERATRAPAI), capture_output=True, encoding="utf-8", errors="replace",
    )
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr)
    return cp.stdout


def load_csv_rules(text: str) -> set[tuple[str, str, str, str]]:
    """Parse fixes CSV text, return set of (taxon, rule, country, state)."""
    rules: set[tuple[str, str, str, str]] = set()
    for row in csv.reader(text.splitlines()):
        if not row or all(c == "" for c in row):
            continue
        if row[0].startswith("#"):
            continue
        if row[0] == "species":
            continue
        if len(row) < 4:
            continue
        rules.add((row[0].lower(), row[1].lower(), row[2], row[3]))
    return rules


def ancestors(taxon: str) -> list[str]:
    """Generate all parent 5-token taxon strings for a 5-token taxon."""
    parts = taxon.split(";")
    out: list[str] = []
    for depth in range(1, 5):
        # Keep first `depth` non-empty tokens, pad with empties.
        non_empty = [t for t in parts if t]
        if depth >= len(non_empty):
            break
        new = non_empty[:depth] + [""] * (5 - depth)
        out.append(";".join(new))
    return out


def main() -> int:
    print("loading geofence files ...")
    cur_base = json.loads(CURRENT_BASE.read_text(encoding="utf-8"))
    main_base = json.loads(git_show("main", "data/geofence_base.json"))
    cur_release = json.loads(CURRENT_RELEASE.read_text(encoding="utf-8"))
    main_release = json.loads(MAIN_RELEASE.read_text(encoding="utf-8"))
    cur_fixes = load_csv_rules(CURRENT_FIXES.read_text(encoding="utf-8"))
    main_fixes = load_csv_rules(git_show("main", "data/geofence_fixes.csv"))
    new_fixes = cur_fixes - main_fixes
    new_fixes_by_taxon: dict[str, list] = defaultdict(list)
    for r in new_fixes:
        new_fixes_by_taxon[r[0]].append(r)
    # Rules that are on BOTH branches' CSVs but only reflected in
    # current's release (because main's release wasn't rebuilt after
    # those rules were added to the CSV).  This catches the "post-release
    # additions" case the user warned us about.
    pending_on_main_by_taxon: dict[str, list] = defaultdict(list)
    for r in (cur_fixes & main_fixes):
        pending_on_main_by_taxon[r[0]].append(r)

    print(f"new fixes-CSV rules on current vs main: {len(new_fixes)}")
    print(f"taxa with at least one such rule:       {len(new_fixes_by_taxon)}")
    print(f"shared rules (pending-on-main candidates): {sum(len(v) for v in pending_on_main_by_taxon.values())}")

    # Load the diffs and bucket each taxon (we aggregate by taxon since
    # category is taxon-level).
    diffs_by_taxon: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
    with DIFFS_CSV.open("r", encoding="utf-8", newline="") as f:
        r = csv.reader(f)
        next(r)
        for taxon, country, admin1, mb, cb in r:
            diffs_by_taxon[taxon].append((country, admin1, mb, cb))

    cat_count = Counter()
    cat_taxa: dict[str, list[str]] = defaultdict(list)
    for taxon in diffs_by_taxon:
        cat = classify(
            taxon, diffs_by_taxon[taxon],
            cur_base, main_base, cur_release, main_release,
            new_fixes_by_taxon, pending_on_main_by_taxon,
        )
        cat_count[cat] += len(diffs_by_taxon[taxon])
        cat_taxa[cat].append(taxon)

    total_diffs = sum(cat_count.values())
    print()
    print(f"{'category':<28s} {'rows':>10s} {'taxa':>6s}")
    for cat, n in cat_count.most_common():
        print(f"{cat:<28s} {n:>10d} {len(cat_taxa[cat]):>6d}")
    print(f"{'TOTAL':<28s} {total_diffs:>10d} {len(diffs_by_taxon):>6d}")

    # Dump unexplained taxa for follow-up.
    out = Path(r"G:/temp/speciesnet-geofence-review-data/diffs-unexplained.txt")
    with out.open("w", encoding="utf-8") as f:
        for taxon in cat_taxa.get("unexplained", []):
            n = len(diffs_by_taxon[taxon])
            f.write(f"{n:5d}  {taxon}\n")
    print(f"\nunexplained taxa written to {out}")
    return 0


def classify(
    taxon, diffs,
    cur_base, main_base, cur_release, main_release,
    new_fixes_by_taxon, pending_on_main_by_taxon,
) -> str:
    # 1. Manual base edits.
    in_cur_base = taxon in cur_base
    in_main_base = taxon in main_base
    if in_cur_base != in_main_base:
        return "base_renamed"
    if in_cur_base and cur_base[taxon] != main_base[taxon]:
        return "base_changed"

    # 2. CSV rule added directly for this taxon.
    if taxon in new_fixes_by_taxon:
        return "csv_rule_added"

    # 2b. CSV rule that exists on both branches but main's release
    #     hasn't been rebuilt to reflect it yet.
    if taxon in pending_on_main_by_taxon:
        return "csv_rule_pending_on_main"

    # 3. CSV rule added for an ancestor (propagates down).
    for anc in ancestors(taxon):
        if anc in new_fixes_by_taxon:
            return "csv_rule_ancestor"
        if anc in pending_on_main_by_taxon:
            return "csv_rule_ancestor_pending"

    # 3b. CSV rule for a descendant (propagates up): an allow rule on a
    #     species can change its genus / family's allow list.  Detected
    #     by checking whether `taxon` is itself a strict ancestor of any
    #     rule's taxon.
    taxon_no_trail = taxon.rstrip(";")
    for r_taxon in new_fixes_by_taxon:
        if r_taxon.startswith(taxon_no_trail + ";") and r_taxon != taxon:
            return "csv_rule_descendant"
    for r_taxon in pending_on_main_by_taxon:
        if r_taxon.startswith(taxon_no_trail + ";") and r_taxon != taxon:
            return "csv_rule_descendant_pending"

    # 4. Bug #8 (block-down overwrites allow): taxon has allow in current
    #    release but not in main release.
    cur_has_allow = "allow" in (cur_release.get(taxon) or {})
    main_has_allow = "allow" in (main_release.get(taxon) or {})
    if cur_has_allow and not main_has_allow:
        return "ancestor_allow_restored"

    # 5. Bug #1 (country-wide widening): allow.country went from
    #    state-restricted on main to countrywide ([]) on current for some
    #    country mentioned in the diffs.
    cur_allow = ((cur_release.get(taxon) or {}).get("allow") or {})
    main_allow = ((main_release.get(taxon) or {}).get("allow") or {})
    for country, _, _, _ in diffs:
        cur_v = cur_allow.get(country)
        main_v = main_allow.get(country)
        if cur_v == [] and isinstance(main_v, list) and len(main_v) > 0:
            return "cw_widening"

    return "unexplained"


if __name__ == "__main__":
    sys.exit(main())
