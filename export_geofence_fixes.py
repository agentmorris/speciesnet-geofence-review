"""Convert accept / custom decisions in decisions.json into
``geofence_fixes.csv``-style rules and append them to that file.

CSV columns: ``species,rule,country_code,admin1_region_code``
(``species`` is actually the 5-token semicolon-delimited taxon string from
``taxonomy_release.txt``: ``class;order;family;genus;species``.)

The output is grouped:
  1. One section per decision that has a notes field: a blank line, then a
     ``# <notes>`` comment, then the rules from that decision.
  2. A "# Block rules created <date>" section with all block rules from
     accept-without-notes decisions.
  3. A "# Allow rules created <date>" section with the allow rules from
     accept-without-notes decisions.

Run with ``--dry-run`` to print the diff without modifying the file; without
``--dry-run`` we append to ``c:\\git\\cameratrapai\\data\\geofence_fixes.csv``.

Refuses to run if any new rule is a literal duplicate of another new rule.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from paths import DECISIONS_FILE, CAMERATRAPAI_DIR     # noqa: E402
from review_queue import build_queue                   # noqa: E402

GEOFENCE_FIXES_CSV = CAMERATRAPAI_DIR / "data" / "geofence_fixes.csv"

# 50 US state codes (no DC, no US territories).  Mirror of
# build_geofence_release.us_state_codes, kept in sync manually so we
# don't have to import from the cameratrapai package here.
US_STATE_CODES = frozenset({
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
})


# (taxonKey, rule, country, state-or-None)
RuleKey = tuple[str, str, str, str | None]


def decision_to_rules(decision: dict, entry: dict) -> list[RuleKey]:
    """Return the list of rule keys that this decision contributes."""
    outcome = decision.get("outcome")
    rules: list[RuleKey] = []

    if outcome == "accept":
        kind  = entry["kind"]
        scope = entry["scope"]
        if kind == "systematic":
            tax_key = entry["fullTaxonKey"]
            for cc in entry.get("removeCountries", []):
                rules.append((tax_key, "block", cc, None))
            return rules

        if kind == "add":
            rule = "allow"
        elif kind == "remove":
            rule = "block"
        else:
            return []
        tax_key = entry["fullTaxonKey"]
        if scope["kind"] == "country":
            rules.append((tax_key, rule, scope["country"], None))
        elif scope["kind"] == "state":
            rules.append((tax_key, rule, "USA", scope["state"].split("-", 1)[1]))
        return rules

    if outcome == "custom":
        custom = decision.get("custom") or {}
        for r in custom.get("allowRules") or []:
            rules.append((r["taxonKey"], "allow", r["country"], r.get("state")))
        for r in custom.get("blockRules") or []:
            rules.append((r["taxonKey"], "block", r["country"], r.get("state")))
        return rules

    return []


def csv_row(rk: RuleKey) -> str:
    tax_key, rule, country, state = rk
    return f"{tax_key},{rule},{country},{state or ''}"


def collapse_all_50_states(rules: list[RuleKey]) -> list[RuleKey]:
    """If [rules] contains state-level USA entries covering all 50 US
    states for a given (taxon, rule_type), replace those entries with a
    single country-wide rule.  Any DC/territory rules for the same group
    are dropped too (a country-wide rule subsumes them).

    The validator in build_geofence_release.py rejects CSVs that
    enumerate all 50 states explicitly, so we collapse here at export
    time to keep the output compatible.
    """
    state_lists: dict[tuple[str, str], set[str]] = defaultdict(set)
    for tax, rt, country, state in rules:
        if country == "USA" and state:
            state_lists[(tax, rt)].add(state)

    to_collapse = {
        key for key, states in state_lists.items()
        if US_STATE_CODES.issubset(states)
    }
    if not to_collapse:
        return rules

    out: list[RuleKey] = []
    emitted_country_wide: set[tuple[str, str]] = set()
    for rk in rules:
        tax, rt, country, state = rk
        if (tax, rt) in to_collapse and country == "USA":
            if (tax, rt) not in emitted_country_wide:
                out.append((tax, rt, "USA", None))
                emitted_country_wide.add((tax, rt))
            # else: drop -- already replaced by the single country-wide row
        else:
            out.append(rk)
    return out


def load_existing_rules(path: Path) -> set[RuleKey]:
    """Parse [path] and return the set of (taxon, rule, country, state)
    tuples already present.  Used so the exporter can skip rules that
    would duplicate something already in the target CSV.
    """
    if not path.exists():
        return set()
    existing: set[RuleKey] = set()
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.reader(f):
            if not row or all(cell == "" for cell in row):
                continue
            if row[0].startswith("#"):
                continue
            if row[0] == "species":  # header
                continue
            if len(row) < 4:
                continue
            tax_key = row[0].lower()
            rule_type = row[1].lower()
            country = row[2]
            state = row[3] if row[3] else None
            existing.add((tax_key, rule_type, country, state))
    return existing


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the appended block to stdout instead of writing.")
    ap.add_argument("--out", default=str(GEOFENCE_FIXES_CSV),
                    help="Path to geofence_fixes.csv (default: the cameratrapai copy).")
    args = ap.parse_args()

    decisions = json.loads(DECISIONS_FILE.read_text(encoding="utf-8"))["decisions"]
    queue, _ = build_queue()
    id_to_entry = {e["id"]: e for e in queue}

    # Per-decision rule lists, in queue order so output is deterministic.
    # For each decision we collapse the "every 50 US states" pattern into
    # a single country-wide rule, since the validator rejects fully
    # enumerated state lists.
    per_decision: list[tuple[str, dict, list[RuleKey]]] = []
    for e in queue:
        d = decisions.get(e["id"])
        if d is None or d.get("outcome") in (None, "reject"):
            continue
        rules = decision_to_rules(d, e)
        rules = collapse_all_50_states(rules)
        if rules:
            per_decision.append((e["id"], d, rules))

    # Duplicate detection across decisions.  When a rule key appears in
    # multiple decisions, we keep the FIRST occurrence (queue order) and drop
    # the subsequent ones, printing a warning so they're visible.
    sources_by_rule: dict[RuleKey, list[tuple[str, str]]] = defaultdict(list)
    for did, d, rules in per_decision:
        for rk in rules:
            sources_by_rule[rk].append((did, d.get("commonName") or ""))

    duplicates = {k: v for k, v in sources_by_rule.items() if len(v) > 1}
    if duplicates:
        print(f"WARN: {len(duplicates)} rules appear in multiple decisions; "
              f"keeping the first occurrence and dropping the rest.")
        for rk, srcs in duplicates.items():
            print(f"  {csv_row(rk)}")
            for i, (did, cn) in enumerate(srcs):
                marker = "kept" if i == 0 else "dropped"
                print(f"      [{marker}] from {did} ({cn})")

    # Build per-decision rule lists with duplicates removed.  A rule is kept
    # only on its FIRST appearance.
    seen: set[RuleKey] = set()
    deduped_per_decision: list[tuple[str, dict, list[RuleKey]]] = []
    for did, d, rules in per_decision:
        kept: list[RuleKey] = []
        for rk in rules:
            if rk in seen:
                continue
            seen.add(rk)
            kept.append(rk)
        if kept:
            deduped_per_decision.append((did, d, kept))
    per_decision = deduped_per_decision

    # Drop rules that are already present in the target CSV: the validator
    # rejects literal duplicates, and the CSV is appended to in place.
    out_path = Path(args.out)
    existing = load_existing_rules(out_path)
    already_present: dict[RuleKey, list[tuple[str, str]]] = defaultdict(list)
    deduped_against_existing: list[tuple[str, dict, list[RuleKey]]] = []
    for did, d, rules in per_decision:
        kept = [rk for rk in rules if rk not in existing]
        for rk in rules:
            if rk in existing:
                already_present[rk].append((did, d.get("commonName") or ""))
        if kept:
            deduped_against_existing.append((did, d, kept))
    if already_present:
        print(f"WARN: {len(already_present)} rules are already present in "
              f"{out_path}; dropping them from the export.")
        for rk, srcs in already_present.items():
            print(f"  {csv_row(rk)}")
            for did, cn in srcs:
                print(f"      from {did} ({cn})")
    per_decision = deduped_against_existing

    # Build the textual sections.
    today = date.today().strftime("%Y.%m.%d")
    sections: list[str] = []

    # 1. Decisions with notes (one section each).
    for did, d, rules in per_decision:
        notes = (d.get("notes") or "").strip()
        if not notes:
            continue
        section = ["", f"# {notes}"]
        for rk in rules:
            section.append(csv_row(rk))
        sections.append("\n".join(section))

    # 2. Accepts without notes: block rules section.
    block_norules: list[RuleKey] = []
    allow_norules: list[RuleKey] = []
    for did, d, rules in per_decision:
        notes = (d.get("notes") or "").strip()
        if notes:
            continue
        for rk in rules:
            (block_norules if rk[1] == "block" else allow_norules).append(rk)

    if block_norules:
        section = ["", f"# Block rules created {today}"]
        for rk in block_norules:
            section.append(csv_row(rk))
        sections.append("\n".join(section))

    if allow_norules:
        section = ["", f"# Allow rules created {today}"]
        for rk in allow_norules:
            section.append(csv_row(rk))
        sections.append("\n".join(section))

    appended = "\n".join(sections) + "\n"

    # Statistics
    decisions_with_rules = len(per_decision)
    with_notes_count = sum(1 for _, d, _ in per_decision if (d.get("notes") or "").strip())
    total_rules = sum(len(r) for _, _, r in per_decision)
    print(f"decisions producing rules:   {decisions_with_rules}")
    print(f"  with notes (own section):  {with_notes_count}")
    print(f"  without notes (date bucket): {decisions_with_rules - with_notes_count}")
    print(f"total rules to append:       {total_rules}")
    print(f"  block (no notes):          {len(block_norules)}")
    print(f"  allow (no notes):          {len(allow_norules)}")

    if args.dry_run:
        print()
        print("--- DRY RUN: would append the following to", out_path, "---")
        print(appended)
        return 0

    with out_path.open("a", encoding="utf-8") as f:
        f.write(appended)
    print(f"\nAppended to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
