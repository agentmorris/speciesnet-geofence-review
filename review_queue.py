"""Build the flattened review queue from the four suggestion files.

Each queue entry has a stable string id and enough context for the UI to
display it and route the user's decision back to a sensible scope.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from countries import country_name, state_name, region_display
from paths import (
    SUGGESTION_FILES, TAXONOMY_FILE, LABELS_FILE, GEOFENCE_FILE,
)

# Items the verifier flagged as auto-handled or quirky. Excluded from the UI
# queue.  Keep these in sync with verify_suggestions.py's inconsistency report.
HIDDEN_REDUNDANT_STATE_REMOVE_BINOMIALS = {
    "meles meles",          # eurasian badger
    "sciurus vulgaris",     # eurasian red squirrel
    "capreolus capreolus",  # european roe deer
}
HIDDEN_ITEM_BY_KEY = {
    # (source, scope, suggestionKind, itemId)
    ("canada", "CAN",     "removeSuggestions",
        "5bb21a74-92cf-4eb8-b32c-a3b4e6f49d36"),   # domestic water buffalo
    ("usa",    "USA",     "addSuggestions",
        "e415387d-26d7-4ef1-9c47-539870f7429b"),   # sika deer
}


# ---------------------------------------------------------------------------
# Taxonomy

def _parse_tax_line(line: str) -> tuple[str, dict[str, str]] | None:
    parts = line.rstrip("\n").split(";")
    if len(parts) != 7:
        return None
    guid, klass, order, family, genus, species, common = parts
    return guid, {
        "guid":    guid,
        "class":   klass,
        "order":   order,
        "family":  family,
        "genus":   genus,
        "species": species,
        "common":  common,
        "full":    ";".join((klass, order, family, genus, species)),
    }


def load_taxonomy() -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    """Returns (by_guid, binomial -> canonical_guid)."""
    by_guid: dict[str, dict[str, str]] = {}
    binom_to_guids: dict[str, list[str]] = {}
    in_release: set[str] = set()

    for path, mark_release in ((TAXONOMY_FILE, True), (LABELS_FILE, False)):
        with path.open("r", encoding="utf-8") as f:
            for raw in f:
                parsed = _parse_tax_line(raw)
                if parsed is None:
                    continue
                guid, info = parsed
                if mark_release:
                    in_release.add(guid)
                if guid not in by_guid:
                    by_guid[guid] = info
                binom = f"{info['genus']} {info['species']}".strip()
                if binom:
                    binom_to_guids.setdefault(binom, []).append(guid)

    canonical: dict[str, str] = {}
    for binom, guids in binom_to_guids.items():
        preferred = [g for g in guids if g in in_release]
        canonical[binom] = preferred[0] if preferred else guids[0]
    return by_guid, canonical


def load_geofence() -> dict[str, dict[str, Any]]:
    return json.loads(GEOFENCE_FILE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Taxonomy helpers exposed to the UI

def lineage(tax: dict[str, str]) -> dict[str, str]:
    """Return a class/order/family/genus/species/binomial dict, with empty
    values for any taxonomic ranks not in the record."""
    return {
        "class":    tax.get("class", ""),
        "order":    tax.get("order", ""),
        "family":   tax.get("family", ""),
        "genus":    tax.get("genus", ""),
        "species":  tax.get("species", ""),
        "common":   tax.get("common", ""),
        "binomial": f"{tax.get('genus','')} {tax.get('species','')}".strip(),
        "full":     tax.get("full", ""),
    }


def summarize_geofence(geofence: dict[str, dict[str, Any]],
                       tax: dict[str, str]) -> dict[str, Any]:
    entry = geofence.get(tax.get("full", ""))
    if entry is None:
        return {"hasEntry": False}
    out: dict[str, Any] = {"hasEntry": True}
    if "allow" in entry:
        out["allow"] = {cc: list(v) for cc, v in entry["allow"].items()}
        out["allowCountryCount"] = len(entry["allow"])
    if "block" in entry:
        out["block"] = {cc: list(v) for cc, v in entry["block"].items()}
        out["blockCountryCount"] = len(entry["block"])
    return out


# ---------------------------------------------------------------------------
# Queue entries

def _resolve(item: dict[str, Any],
             tax_by_guid: dict[str, dict[str, str]],
             binom_to_guid: dict[str, str]) -> tuple[dict[str, str] | None, bool]:
    """Return (taxonomy_record, used_binomial_fallback)."""
    guid = item.get("itemId")
    tax = tax_by_guid.get(guid)
    if tax is not None:
        return tax, False
    binom = (item.get("binomial") or "").strip()
    g = binom_to_guid.get(binom)
    if g is None:
        return None, False
    return tax_by_guid[g], True


def _search_region_word(scope: dict[str, Any]) -> str:
    """The region phrase used in search queries -- no parenthetical suffixes."""
    if scope.get("kind") == "global":
        return "range"
    if scope.get("kind") == "country":
        return country_name(scope["country"])
    if scope.get("kind") == "state":
        return state_name(scope["state"])
    return ""


def _make_search_links(common: str,
                       binomial: str,
                       tax: dict[str, str] | None,
                       scope: dict[str, Any]) -> list[dict[str, str]]:
    """Build Google search links across taxonomic levels.

    For systematic (scope.kind == 'global'), every query ends with the word
    'range'; for region-scoped items it ends with the country or state name.
    The common-name query uses 'in <region>' for readability; all other
    queries just append the region word.
    """
    import urllib.parse as up
    def gq(q: str) -> str:
        return "https://www.google.com/search?q=" + up.quote_plus(q)

    region_word = _search_region_word(scope)
    is_systematic = scope.get("kind") == "global"

    queries: list[tuple[str, str]] = []
    if common:
        if is_systematic:
            queries.append((f"{common} {region_word}", "Google"))
        else:
            queries.append((f"{common} in {region_word}", "Google"))
    if binomial:
        queries.append((f"{binomial} {region_word}", "Google (binomial)"))
    if tax:
        genus  = (tax.get("genus")  or "").strip()
        family = (tax.get("family") or "").strip()
        # For systematic items, use the literal word "genus" / "family" rather
        # than the generic "range" suffix -- a more specific search.
        genus_suffix  = "genus"  if is_systematic else region_word
        family_suffix = "family" if is_systematic else region_word
        if genus:
            queries.append((f"{genus} {genus_suffix}", "Google (genus)"))
        if family:
            queries.append((f"{family} {family_suffix}", "Google (family)"))

    return [{"label": lab, "url": gq(q)} for q, lab in queries]


def _make_proposed_rules(item: dict[str, Any],
                         kind: str,
                         scope: dict[str, Any],
                         tax: dict[str, str] | None) -> dict[str, list[dict[str, Any]]]:
    """Return {'allowRules': [...], 'blockRules': [...]}, each a list of rule
    objects describing the *change* the proposal would apply.  No 'keep' /
    no-op entries.
    """
    binom  = (item.get("binomial") or "").strip()
    common = (item.get("commonName") or "").strip()
    canonical_common = (tax.get("common", "").strip() if tax else common)
    full   = tax.get("full") if tax else ""
    label  = canonical_common or common or binom

    def rule(country: str | None, state: str | None) -> dict[str, Any]:
        return {
            "taxonLevel": "species",
            "binomial":   binom,
            "taxonKey":   full,
            "country":    country,
            "state":      state,
        }

    allow_rules: list[dict[str, Any]] = []
    block_rules: list[dict[str, Any]] = []

    if kind == "systematic":
        for cc in item.get("removeCountries", []) or []:
            block_rules.append(rule(country=cc, state=None))
        for cc in item.get("addCountries", []) or []:   # absent in current data
            allow_rules.append(rule(country=cc, state=None))
    elif kind == "add":
        if scope["kind"] == "state":
            allow_rules.append(rule(country="USA",
                                    state=scope["state"].split("-", 1)[1]))
        else:
            allow_rules.append(rule(country=scope.get("country"), state=None))
    elif kind == "remove":
        if scope["kind"] == "state":
            block_rules.append(rule(country="USA",
                                    state=scope["state"].split("-", 1)[1]))
        else:
            block_rules.append(rule(country=scope.get("country"), state=None))

    return {"allowRules": allow_rules, "blockRules": block_rules}


def _region_label(scope: dict[str, str]) -> str:
    kind = scope["kind"]
    if kind == "country":
        return scope["country"]
    if kind == "state":
        return scope["state"]
    return "global"


def _collect_country_names(entry: dict[str, Any]) -> dict[str, str]:
    """Build {ISO3 -> readable name} for every country code this entry mentions
    (so the UI can look up names without an extra round trip)."""
    codes: set[str] = set()
    for cc in entry.get("keepCountries", []) or []:
        codes.add(cc)
    for cc in entry.get("removeCountries", []) or []:
        codes.add(cc)
    gf = entry.get("geofence") or {}
    for cc in (gf.get("allow") or {}).keys():
        codes.add(cc)
    for cc in (gf.get("block") or {}).keys():
        codes.add(cc)
    if entry.get("scope", {}).get("country"):
        codes.add(entry["scope"]["country"])
    return {cc: country_name(cc) for cc in sorted(codes)}


def _make_entry(*,
                source: str,
                index_in_source: int,
                item: dict[str, Any],
                kind: str,                # "add" / "remove" / "systematic"
                scope: dict[str, Any],
                tax: dict[str, str] | None,
                used_binomial: bool,
                extra: dict[str, Any] | None = None) -> dict[str, Any]:
    bin_   = (item.get("binomial") or "").strip()
    common = (item.get("commonName") or "").strip()
    canonical_common = (tax.get("common", "").strip() if tax else common)
    region_label   = _region_label(scope)
    region_display_str = region_display(scope)
    entry: dict[str, Any] = {
        "id":              _entry_id(source, kind, scope, item),
        "source":          source,
        "kind":            kind,
        "scope":           scope,
        "regionLabel":     region_label,
        "regionDisplay":   region_display_str,
        "itemId":          item.get("itemId"),
        "binomial":        bin_,
        "commonName":      canonical_common or common,
        "commonNameInSuggestion": common if common != canonical_common else None,
        "classLabel":      item.get("classLabel"),
        "lineage":         lineage(tax) if tax else None,
        "fullTaxonKey":    tax.get("full") if tax else None,
        "usedBinomialFallback": used_binomial,
        "indexInSource":   index_in_source,
        "searchLinks":     _make_search_links(canonical_common or common, bin_, tax, scope),
        "proposedRules":   _make_proposed_rules(item, kind, scope, tax),
    }
    # carry forward useful UI fields
    for k in ("status", "bucket", "footprintCode", "footprintLabel", "expected"):
        if k in item:
            entry[k] = item[k]
    if extra:
        entry.update(extra)
    return entry


def _entry_id(source: str, kind: str, scope: dict[str, Any], item: dict[str, Any]) -> str:
    iid = item.get("itemId", "")
    if scope["kind"] == "country":
        return f"{source}:{scope['country']}:{kind}:{iid}"
    if scope["kind"] == "state":
        return f"{source}:{scope['state']}:{kind}:{iid}"
    return f"{source}:{kind}:{item.get('rank', iid)}"


def _is_hidden(source: str, scope: dict[str, Any], kind: str, item: dict[str, Any]) -> bool:
    # Redundant state-level removes for three country-blocked species.
    if (source == "usa_state" and kind == "remove"
            and item.get("binomial") in HIDDEN_REDUNDANT_STATE_REMOVE_BINOMIALS):
        return True
    sugg_kind = "addSuggestions" if kind == "add" else "removeSuggestions"
    scope_str = scope.get("country") or scope.get("state") or ""
    if (source, scope_str, sugg_kind, item.get("itemId")) in HIDDEN_ITEM_BY_KEY:
        return True
    return False


def build_queue() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Returns (queue, context).  Context includes loaded taxonomy + geofence."""
    tax_by_guid, binom_to_guid = load_taxonomy()
    geofence = load_geofence()

    queue: list[dict[str, Any]] = []

    # --- systematic
    sys_path = SUGGESTION_FILES["systematic"]
    sys_data = json.loads(sys_path.read_text(encoding="utf-8"))
    for idx, item in enumerate(sys_data.get("items", [])):
        scope = {"kind": "global"}
        if _is_hidden("systematic", scope, "systematic", item):
            continue
        tax, used = _resolve(item, tax_by_guid, binom_to_guid)
        entry = _make_entry(
            source="systematic",
            index_in_source=idx,
            item=item,
            kind="systematic",
            scope=scope,
            tax=tax,
            used_binomial=used,
            extra={
                "rank":             item.get("rank"),
                "proposalSummary":  item.get("proposalSummary"),
                "keepCountries":    list(item.get("keepCountries", [])),
                "removeCountries":  list(item.get("removeCountries", [])),
                "reviewCountryCount":   item.get("reviewCountryCount"),
                "keepCountryCount":     item.get("keepCountryCount"),
                "removeCountryCount":   item.get("removeCountryCount"),
            },
        )
        if tax:
            entry["geofence"] = summarize_geofence(geofence, tax)
        entry["countryNames"] = _collect_country_names(entry)
        queue.append(entry)

    # --- per-country files
    for source in ("canada", "usa"):
        data = json.loads(SUGGESTION_FILES[source].read_text(encoding="utf-8"))
        country = data["generatedFor"]
        for kind_key, ui_kind in (("addSuggestions", "add"),
                                  ("removeSuggestions", "remove")):
            for idx, item in enumerate(data.get(kind_key, [])):
                scope = {"kind": "country", "country": country}
                if _is_hidden(source, scope, ui_kind, item):
                    continue
                tax, used = _resolve(item, tax_by_guid, binom_to_guid)
                entry = _make_entry(
                    source=source,
                    index_in_source=idx,
                    item=item,
                    kind=ui_kind,
                    scope=scope,
                    tax=tax,
                    used_binomial=used,
                )
                if tax:
                    entry["geofence"] = summarize_geofence(geofence, tax)
                queue.append(entry)

    # --- per-state file
    state_data = json.loads(SUGGESTION_FILES["usa_state"].read_text(encoding="utf-8"))
    for state_block in state_data.get("states", []):
        generated_for = state_block["generatedFor"]
        for kind_key, ui_kind in (("addSuggestions", "add"),
                                  ("removeSuggestions", "remove")):
            for idx, item in enumerate(state_block.get(kind_key, [])):
                scope = {"kind": "state", "state": generated_for}
                if _is_hidden("usa_state", scope, ui_kind, item):
                    continue
                tax, used = _resolve(item, tax_by_guid, binom_to_guid)
                entry = _make_entry(
                    source="usa_state",
                    index_in_source=idx,
                    item=item,
                    kind=ui_kind,
                    scope=scope,
                    tax=tax,
                    used_binomial=used,
                )
                if tax:
                    entry["geofence"] = summarize_geofence(geofence, tax)
                queue.append(entry)

    context = {
        "taxonomy": tax_by_guid,
        "geofence": geofence,
    }
    return queue, context


if __name__ == "__main__":
    q, _ = build_queue()
    print(f"queue length: {len(q)}")
    by_source: dict[str, int] = {}
    for e in q:
        by_source[e["source"]] = by_source.get(e["source"], 0) + 1
    for k, v in by_source.items():
        print(f"  {k}: {v}")
