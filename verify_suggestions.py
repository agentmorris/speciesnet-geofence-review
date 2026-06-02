"""Validate the four geofence-review suggestion files against the current
taxonomy and geofence.

The suggestion files were generated against the precomputed country/state packs
in cameratrapai (effectively only the ``allow`` rules of geofence_release.json,
ignoring ``block``). We mirror that model for the "is this species currently
in the country/state pack?" check, and additionally flag any case where the
``block`` rule would have changed the answer.

Outputs:
  * stdout: summary
  * G:\\temp\\speciesnet-geofence-review-data\\verification_mismatches.json
    -- per-mismatch detail
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from paths import (
    SUGGESTION_FILES, TAXONOMY_FILE, LABELS_FILE, GEOFENCE_FILE,
    VERIFICATION_MISMATCHES_FILE       as OUT_MISMATCHES,
    VERIFICATION_INCONSISTENCIES_FILE  as OUT_INCONSISTENCIES,
    VERIFICATION_INCONSISTENCIES_MD    as OUT_INCONSISTENCIES_MD,
)


# ---------------------------------------------------------------------------
# Taxonomy

def _parse_tax_line(line: str) -> tuple[str, dict[str, str]] | None:
    parts = line.rstrip("\n").split(";")
    if len(parts) != 7:
        return None
    guid, klass, order, family, genus, species, common = parts
    return guid, {
        "class":   klass,
        "order":   order,
        "family":  family,
        "genus":   genus,
        "species": species,
        "common":  common,
        "full":    ";".join((klass, order, family, genus, species)),
    }


def load_taxonomy(taxonomy_path: Path,
                  labels_path: Path) -> tuple[dict[str, dict[str, str]],
                                              dict[str, str]]:
    """Returns (by_guid, by_binomial->guid).

    Loads taxonomy_release.txt and supplements with any GUIDs from
    labels.txt that aren't already in the taxonomy.  by_binomial maps
    "genus species" -> a chosen canonical GUID; if multiple GUIDs share a
    binomial we prefer the one from taxonomy_release.txt.
    """
    by_guid: dict[str, dict[str, str]] = {}
    binomial_to_guids: dict[str, list[str]] = {}

    for path in (taxonomy_path, labels_path):
        with path.open("r", encoding="utf-8") as f:
            for raw in f:
                if not raw.strip():
                    continue
                parsed = _parse_tax_line(raw)
                if parsed is None:
                    continue
                guid, info = parsed
                if guid not in by_guid:
                    by_guid[guid] = info
                binom = f"{info['genus']} {info['species']}".strip()
                if binom:
                    binomial_to_guids.setdefault(binom, []).append(guid)

    # Choose canonical GUID per binomial: prefer those in taxonomy_release.txt.
    canonical: dict[str, str] = {}
    in_release = set()
    with taxonomy_path.open("r", encoding="utf-8") as f:
        for raw in f:
            parsed = _parse_tax_line(raw)
            if parsed:
                in_release.add(parsed[0])
    for binom, guids in binomial_to_guids.items():
        preferred = [g for g in guids if g in in_release]
        canonical[binom] = preferred[0] if preferred else guids[0]
    return by_guid, canonical


# ---------------------------------------------------------------------------
# Geofence

def load_geofence(path: Path) -> dict[str, dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def is_in_country_pack(geofence: dict[str, dict[str, Any]],
                       taxon_key: str,
                       country: str) -> bool:
    """Mirror of the precomputed country-pack semantics.

    Empirically the suggestion-file generator decides "in pack" using:
      * if the entry has an ``allow`` block: ``country`` must be a key
        (regardless of admin1 restrictions in the value).  ``block`` is
        ignored when ``allow`` is present.
      * if the entry has only ``block``: ``country`` must NOT be a key.
      * if there is no entry: not in any pack (packs only include species
        whose geofence explicitly admits them).
    """
    entry = geofence.get(taxon_key)
    if entry is None:
        return False
    allow = entry.get("allow")
    if allow:
        return country in allow
    block = entry.get("block")
    if block:
        return country not in block
    return False


def is_in_state_pack(geofence: dict[str, dict[str, Any]],
                     taxon_key: str,
                     state: str) -> bool:
    """Is the species in the USA-<state> pack?

    Uses the same allow-or-block rule as :func:`is_in_country_pack`, then
    applies admin1 restrictions.  ``block`` admin1 restrictions are
    intentionally ignored to mirror the suggestion-file generator.
    """
    entry = geofence.get(taxon_key)
    if entry is None:
        return False
    allow = entry.get("allow")
    if allow:
        val = allow.get("USA")
        if val is None:
            return False
        return (val == []) or (state in val)
    block = entry.get("block")
    if block:
        val = block.get("USA")
        if val is None:
            return True                # USA not blocked at all
        if val == []:
            return False               # USA fully blocked
        return state not in val        # state-level block list
    return False


def block_affects(geofence: dict[str, dict[str, Any]],
                  taxon_key: str,
                  country: str,
                  admin1: str | None = None) -> bool:
    """True iff the ``block`` rule for this taxon would change "in-pack" to
    "actually blocked" for this country / optional admin1.
    """
    entry = geofence.get(taxon_key)
    if entry is None:
        return False
    block = entry.get("block")
    if not block:
        return False
    if country not in block:
        return False
    admin1_block = block[country]
    if not admin1_block:
        return True  # whole country blocked
    if admin1 and admin1 in admin1_block:
        return True
    return False


# ---------------------------------------------------------------------------
# Per-item taxonomy check

def resolve_item(item: dict[str, Any],
                 taxonomy: dict[str, dict[str, str]],
                 binomial_to_guid: dict[str, str]) -> tuple[str | None,
                                                            dict[str, str] | None,
                                                            list[dict[str, Any]]]:
    """Resolve an item to (taxonomy_key, taxonomy_record, messages).

    First tries the GUID. If GUID is unknown, falls back to binomial lookup
    and reports a ``stale_guid`` message.  Also reports binomial / common-name
    mismatches when they differ from the resolved taxonomy record.
    """
    msgs: list[dict[str, Any]] = []
    guid_given = item.get("itemId")
    tax = taxonomy.get(guid_given)
    resolved_via_binomial = False
    if tax is None:
        # Fallback by binomial
        binom_given = (item.get("binomial") or "").strip()
        fallback_guid = binomial_to_guid.get(binom_given)
        if fallback_guid is None:
            msgs.append({
                "kind": "guid_not_in_taxonomy",
                "itemId": guid_given,
                "label": item.get("label"),
            })
            return None, None, msgs
        tax = taxonomy[fallback_guid]
        resolved_via_binomial = True
        msgs.append({
            "kind": "stale_guid",
            "itemId": guid_given,
            "label": item.get("label"),
            "resolvedTo": fallback_guid,
        })

    expected_binomial = f"{tax['genus']} {tax['species']}".strip()
    given_binomial = (item.get("binomial") or "").strip()
    if expected_binomial != given_binomial:
        msgs.append({
            "kind": "binomial_mismatch",
            "itemId": guid_given,
            "expected": expected_binomial,
            "got":      given_binomial,
            "resolvedViaBinomial": resolved_via_binomial,
        })

    expected_common = (tax["common"] or "").strip().lower()
    given_common = (item.get("commonName") or "").strip().lower()
    if expected_common and expected_common != given_common:
        msgs.append({
            "kind": "common_name_mismatch",
            "itemId": guid_given,
            "expected": tax["common"],
            "got":      item.get("commonName"),
            "resolvedViaBinomial": resolved_via_binomial,
        })

    return tax["full"], tax, msgs


# ---------------------------------------------------------------------------
# Per-suggestion geofence check

def check_regional_file(filename: str,
                        data: dict[str, Any],
                        geofence: dict[str, dict[str, Any]],
                        taxonomy: dict[str, dict[str, str]],
                        binomial_to_guid: dict[str, str]) -> tuple[list[dict[str, Any]], Counter]:
    out: list[dict[str, Any]] = []
    counts: Counter = Counter()
    country = data["generatedFor"]

    for suggestion_kind in ("addSuggestions", "removeSuggestions"):
        items = data.get(suggestion_kind, [])
        counts[f"{suggestion_kind}_total"] += len(items)
        expected_in_pack = (suggestion_kind == "removeSuggestions")

        for item in items:
            counts["items_checked"] += 1
            key, _tax, msgs = resolve_item(item, taxonomy, binomial_to_guid)
            for m in msgs:
                m["file"] = filename
                m["suggestionKind"] = suggestion_kind
                m["country"] = country
                out.append(m)
                counts[m["kind"]] += 1
            if key is None:
                continue

            in_pack = is_in_country_pack(geofence, key, country)
            if in_pack != expected_in_pack:
                out.append({
                    "kind": "pack_disagrees",
                    "file": filename,
                    "suggestionKind": suggestion_kind,
                    "country": country,
                    "itemId": item.get("itemId"),
                    "label": item.get("label"),
                    "expectedInPack": expected_in_pack,
                    "actuallyInPack": in_pack,
                })
                counts["pack_disagrees"] += 1

            # Internal "expected" cross-check
            embedded_expected = item.get("expected")
            if isinstance(embedded_expected, bool) and embedded_expected != expected_in_pack:
                out.append({
                    "kind": "embedded_expected_disagrees",
                    "file": filename,
                    "suggestionKind": suggestion_kind,
                    "country": country,
                    "itemId": item.get("itemId"),
                    "label": item.get("label"),
                    "expectedFromKind": expected_in_pack,
                    "expectedFromItem": embedded_expected,
                })
                counts["embedded_expected_disagrees"] += 1

            # Block-rule shadow: noteworthy but not blocking
            if expected_in_pack and in_pack and block_affects(geofence, key, country):
                out.append({
                    "kind": "in_pack_but_country_blocked",
                    "file": filename,
                    "suggestionKind": suggestion_kind,
                    "country": country,
                    "itemId": item.get("itemId"),
                    "label": item.get("label"),
                })
                counts["in_pack_but_country_blocked"] += 1

    return out, counts


def check_state_file(filename: str,
                     data: dict[str, Any],
                     geofence: dict[str, dict[str, Any]],
                     taxonomy: dict[str, dict[str, str]],
                     binomial_to_guid: dict[str, str]) -> tuple[list[dict[str, Any]], Counter]:
    out: list[dict[str, Any]] = []
    counts: Counter = Counter()

    for state_block in data.get("states", []):
        generated_for = state_block["generatedFor"]
        if not generated_for.startswith("USA-"):
            out.append({
                "kind": "unexpected_state_code",
                "file": filename,
                "generatedFor": generated_for,
            })
            counts["unexpected_state_code"] += 1
            continue
        state = generated_for.split("-", 1)[1]

        for suggestion_kind in ("addSuggestions", "removeSuggestions"):
            items = state_block.get(suggestion_kind, [])
            counts[f"{suggestion_kind}_total"] += len(items)
            expected_in_pack = (suggestion_kind == "removeSuggestions")

            for item in items:
                counts["items_checked"] += 1
                key, _tax, msgs = resolve_item(item, taxonomy, binomial_to_guid)
                for m in msgs:
                    m["file"] = filename
                    m["suggestionKind"] = suggestion_kind
                    m["state"] = generated_for
                    out.append(m)
                    counts[m["kind"]] += 1
                if key is None:
                    continue

                in_pack = is_in_state_pack(geofence, key, state)
                if in_pack != expected_in_pack:
                    out.append({
                        "kind": "pack_disagrees",
                        "file": filename,
                        "suggestionKind": suggestion_kind,
                        "state": generated_for,
                        "itemId": item.get("itemId"),
                        "label": item.get("label"),
                        "expectedInPack": expected_in_pack,
                        "actuallyInPack": in_pack,
                    })
                    counts["pack_disagrees"] += 1

                embedded_expected = item.get("expected")
                if isinstance(embedded_expected, bool) and embedded_expected != expected_in_pack:
                    out.append({
                        "kind": "embedded_expected_disagrees",
                        "file": filename,
                        "suggestionKind": suggestion_kind,
                        "state": generated_for,
                        "itemId": item.get("itemId"),
                        "label": item.get("label"),
                        "expectedFromKind": expected_in_pack,
                        "expectedFromItem": embedded_expected,
                    })
                    counts["embedded_expected_disagrees"] += 1

                if expected_in_pack and in_pack and block_affects(geofence, key, "USA", state):
                    out.append({
                        "kind": "in_pack_but_state_blocked",
                        "file": filename,
                        "suggestionKind": suggestion_kind,
                        "state": generated_for,
                        "itemId": item.get("itemId"),
                        "label": item.get("label"),
                    })
                    counts["in_pack_but_state_blocked"] += 1

    return out, counts


def check_systematic_file(filename: str,
                          data: dict[str, Any],
                          geofence: dict[str, dict[str, Any]],
                          taxonomy: dict[str, dict[str, str]],
                          binomial_to_guid: dict[str, str]) -> tuple[list[dict[str, Any]], Counter]:
    out: list[dict[str, Any]] = []
    counts: Counter = Counter()

    for item in data.get("items", []):
        counts["items_checked"] += 1
        key, _tax, msgs = resolve_item(item, taxonomy, binomial_to_guid)
        for m in msgs:
            m["file"] = filename
            m["rank"] = item.get("rank")
            out.append(m)
            counts[m["kind"]] += 1
        if key is None:
            continue

        keep   = item.get("keepCountries",   []) or []
        remove = item.get("removeCountries", []) or []

        for bucket_name, codes in (("keep", keep), ("remove", remove)):
            for cc in codes:
                if not is_in_country_pack(geofence, key, cc):
                    out.append({
                        "kind": "pack_disagrees",
                        "file": filename,
                        "rank": item.get("rank"),
                        "itemId": item.get("itemId"),
                        "label": item.get("label"),
                        "country": cc,
                        "bucket": bucket_name,
                        "expectedInPack": True,
                        "actuallyInPack": False,
                    })
                    counts["pack_disagrees"] += 1

        # Light internal-consistency check (counts only; no per-item entries).
        if item.get("keepCountryCount") != len(keep):
            counts["sys_keep_count_mismatch"] += 1
        if item.get("removeCountryCount") != len(remove):
            counts["sys_remove_count_mismatch"] += 1

    return out, counts


# ---------------------------------------------------------------------------
# Main

def main() -> int:
    print("Loading taxonomy + labels ...")
    taxonomy, binomial_to_guid = load_taxonomy(TAXONOMY_FILE, LABELS_FILE)
    print(f"  taxa indexed by GUID: {len(taxonomy)}")
    print(f"  binomials indexed:    {len(binomial_to_guid)}")
    print("Loading geofence ...")
    geofence = load_geofence(GEOFENCE_FILE)
    print(f"  geofence entries: {len(geofence)}")

    all_mismatches: list[dict[str, Any]] = []
    per_file_counts: dict[str, Counter] = {}

    for label, path in SUGGESTION_FILES.items():
        print(f"\nLoading {label}: {path.name}")
        data = json.loads(path.read_text(encoding="utf-8"))
        if label == "systematic":
            ms, c = check_systematic_file(path.name, data, geofence, taxonomy, binomial_to_guid)
        elif label == "usa_state":
            ms, c = check_state_file(path.name, data, geofence, taxonomy, binomial_to_guid)
        else:
            ms, c = check_regional_file(path.name, data, geofence, taxonomy, binomial_to_guid)
        all_mismatches.extend(ms)
        per_file_counts[label] = c
        print(f"  items_checked = {c.get('items_checked', 0)}")
        for k in ("pack_disagrees", "guid_not_in_taxonomy", "stale_guid",
                  "binomial_mismatch", "common_name_mismatch",
                  "embedded_expected_disagrees",
                  "in_pack_but_country_blocked", "in_pack_but_state_blocked"):
            if c.get(k):
                print(f"  {k}: {c[k]}")

    by_kind: Counter = Counter(m["kind"] for m in all_mismatches)

    print("\n=== Summary ===")
    print(f"Total messages: {len(all_mismatches)}")
    print("By kind:")
    for k, v in sorted(by_kind.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {k:35s}  {v}")

    OUT_MISMATCHES.write_text(json.dumps({
        "summary": {
            "totalMessages": len(all_mismatches),
            "byKind": dict(by_kind),
            "perFile": {k: dict(v) for k, v in per_file_counts.items()},
        },
        "mismatches": all_mismatches,
    }, indent=2), encoding="utf-8")
    print(f"\nWrote detailed mismatch report: {OUT_MISMATCHES}")

    write_inconsistency_report(all_mismatches)
    print(f"Wrote inconsistency JSON:       {OUT_INCONSISTENCIES}")
    print(f"Wrote inconsistency Markdown:   {OUT_INCONSISTENCIES_MD}")
    return 0


# ---------------------------------------------------------------------------
# Inconsistency report

def write_inconsistency_report(mismatches: list[dict[str, Any]]) -> None:
    """Write a categorized JSON + Markdown summary of all inconsistencies that
    we plan to handle outside the manual-review UI.
    """
    stale_by_guid: dict[str, dict[str, Any]] = {}
    common_by_guid: dict[str, dict[str, Any]] = {}
    redundant_state_remove_by_label: dict[str, list[dict[str, Any]]] = {}
    isolated_quirks: list[dict[str, Any]] = []

    for m in mismatches:
        kind = m["kind"]
        if kind == "stale_guid":
            guid = m["itemId"]
            rec = stale_by_guid.setdefault(guid, {
                "itemId":     guid,
                "resolvedTo": m["resolvedTo"],
                "label":      m["label"],
                "occurrences": 0,
            })
            rec["occurrences"] += 1
        elif kind == "common_name_mismatch":
            guid = m["itemId"]
            rec = common_by_guid.setdefault(guid, {
                "itemId":          guid,
                "expectedFromTax": m["expected"],
                "givenInSuggest":  m["got"],
                "occurrences":     0,
            })
            rec["occurrences"] += 1
        elif kind == "in_pack_but_state_blocked":
            redundant_state_remove_by_label.setdefault(m["label"], []).append({
                "state":  m["state"],
                "itemId": m["itemId"],
            })
        elif kind in ("pack_disagrees", "embedded_expected_disagrees"):
            isolated_quirks.append(m)

    report = {
        "staleGuids": list(stale_by_guid.values()),
        "commonNameDrift": list(common_by_guid.values()),
        "redundantStateRemove": [
            {"label": label,
             "stateCount": len(occs),
             "states": sorted({o["state"] for o in occs})}
            for label, occs in sorted(redundant_state_remove_by_label.items())
        ],
        "isolatedQuirks": isolated_quirks,
    }
    OUT_INCONSISTENCIES.write_text(json.dumps(report, indent=2), encoding="utf-8")

    # --- Markdown
    md = []
    md.append("# Verification inconsistencies\n")
    md.append("Items in the suggestion files that don't cleanly match the "
              "current `geofence_release.json` + `taxonomy_release.txt`. These "
              "are handled outside the manual-review UI; the UI's queue "
              "excludes them.\n")

    md.append("## Stale GUIDs\n")
    md.append("Items whose `itemId` is missing from `taxonomy_release.txt` "
              "but whose binomial resolves to a known taxon. The UI uses the "
              "binomial.\n")
    md.append("| Old GUID | Resolved GUID | Label | Occurrences |")
    md.append("| --- | --- | --- | ---: |")
    for r in report["staleGuids"]:
        md.append(f"| `{r['itemId']}` | `{r['resolvedTo']}` | {r['label']} | {r['occurrences']} |")
    md.append("")

    md.append("## Common-name drift\n")
    md.append("Same binomial; the suggestion file uses a slightly different "
              "common name from the current taxonomy.\n")
    md.append("| GUID | Common name (taxonomy) | Common name (suggestion) | Occurrences |")
    md.append("| --- | --- | --- | ---: |")
    for r in report["commonNameDrift"]:
        md.append(f"| `{r['itemId']}` | {r['expectedFromTax']} | {r['givenInSuggest']} | {r['occurrences']} |")
    md.append("")

    md.append("## Redundant state-level remove suggestions\n")
    md.append("Species blocked countrywide in the USA via the `block` rule, "
              "but the suggestion-generator (which ignores `block`) "
              "still flagged them for state-by-state removal. Already not "
              "allowed; no action needed.\n")
    md.append("| Species | State count | States |")
    md.append("| --- | ---: | --- |")
    for r in report["redundantStateRemove"]:
        md.append(f"| {r['label']} | {r['stateCount']} | {', '.join(r['states'])} |")
    md.append("")

    md.append("## Isolated quirks\n")
    md.append("Items that disagree with the geofence in ways that don't fit "
              "any of the above patterns. Review individually.\n")
    if not isolated_quirks:
        md.append("_None._\n")
    else:
        for q in isolated_quirks:
            scope = q.get("country") or q.get("state") or f"rank {q.get('rank')}"
            md.append(f"- **{q.get('label')}** "
                      f"(`{q.get('file')}`, scope `{scope}`, kind `{q['kind']}`"
                      + (f", suggestion `{q['suggestionKind']}`" if q.get("suggestionKind") else "")
                      + ")")
            if "expectedInPack" in q:
                md.append(f"    - expected in pack: `{q['expectedInPack']}`, "
                          f"actually in pack: `{q['actuallyInPack']}`")
            if "expectedFromKind" in q:
                md.append(f"    - bucket implies expected=`{q['expectedFromKind']}`, "
                          f"item's own `expected` field says `{q['expectedFromItem']}`")
        md.append("")

    OUT_INCONSISTENCIES_MD.write_text("\n".join(md), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
