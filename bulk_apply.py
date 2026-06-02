"""Apply bulk-update rules described in natural language by the user.

Each "round" is a set of rules.  Running this rewrites `decisions.json`
for matching items: it sets `outcome`, optional `level` / `overrides`,
`notes`, `bulkRound`, and `bulkRuleName`, and bumps `updatedAt`.

Existing decisions for the matching items are overwritten.  Decisions for
items not matched by any rule are left untouched.

Rounds are appended below in chronological order; do not delete past
rounds (they document what was applied and when).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from paths import DATA_DIR as DATA_OUT, DECISIONS_FILE, GEOFENCE_FILE   # noqa: E402
from review_queue import build_queue                                    # noqa: E402


# ---------------------------------------------------------------------------
# Continent groupings (UN M.49 alpha-3, with user-noted edge cases)

AFRICA = {
    "DZA","AGO","BEN","BWA","BFA","BDI","CPV","CMR","CAF","TCD","COM","COG","COD",
    "CIV","DJI","EGY","GNQ","ERI","SWZ","ETH","GAB","GMB","GHA","GIN","GNB","KEN",
    "LSO","LBR","LBY","MDG","MWI","MLI","MRT","MUS","MAR","MOZ","NAM","NER","NGA",
    "RWA","STP","SEN","SYC","SLE","SOM","ZAF","SSD","SDN","TZA","TGO","TUN","UGA",
    "ZMB","ZWE","ESH","MYT","REU","SHN","IOT",
}
ASIA = {
    "AFG","ARM","AZE","BHR","BGD","BTN","BRN","KHM","CHN","CYP","GEO","HKG","IND",
    "IDN","IRN","IRQ","ISR","JPN","JOR","KAZ","KWT","KGZ","LAO","LBN","MAC","MYS",
    "MDV","MNG","MMR","NPL","PRK","OMN","PAK","PSE","PHL","QAT","RUS","SAU","SGP",
    "KOR","LKA","SYR","TWN","TJK","THA","TLS","TUR","TKM","ARE","UZB","VNM","YEM",
}
AFRICA_ASIA = AFRICA | ASIA

ASIA_NO_RUS = ASIA - {"RUS"}

EUROPE = {
    "ALA","ALB","AND","AUT","BEL","BGR","BIH","BLR","CHE","CZE","DEU","DNK",
    "ESP","EST","FIN","FRA","FRO","GBR","GGY","GIB","GRC","HRV","HUN","IMN",
    "IRL","ISL","ITA","JEY","LIE","LTU","LUX","LVA","MCO","MDA","MKD","MLT",
    "MNE","NLD","NOR","POL","PRT","ROU","SJM","SMR","SRB","SVK","SVN","SWE",
    "UKR","VAT",
}

NORTH_AMERICA   = {"CAN", "MEX", "USA"}
CENTRAL_AMERICA = {"BLZ", "CRI", "GTM", "HND", "NIC", "PAN", "SLV"}
SOUTH_AMERICA   = {"ARG","BOL","BRA","CHL","COL","ECU","FLK","GUF","GUY",
                   "PER","PRY","SUR","URY","VEN"}
CARIBBEAN       = {"ABW","AIA","ATG","BES","BHS","BLM","BRB","CUB","CUW",
                   "CYM","DMA","DOM","GLP","GRD","HTI","JAM","KNA","LCA",
                   "MAF","MSR","MTQ","PRI","SXM","TCA","TTO","VCT","VGB","VIR"}

WESTERN_HEMISPHERE = (NORTH_AMERICA | CENTRAL_AMERICA | SOUTH_AMERICA
                     | CARIBBEAN | {"GRL", "SPM", "BMU"})

ALL_COUNTRIES = set()  # populated lazily from the geofence file


def _load_all_countries() -> set[str]:
    """All ISO3 country codes ever referenced by the geofence file."""
    global ALL_COUNTRIES
    if ALL_COUNTRIES:
        return ALL_COUNTRIES
    g = json.loads(GEOFENCE_FILE.read_text(encoding="utf-8"))
    codes: set[str] = set()
    for entry in g.values():
        for d in (entry.get("allow") or {}, entry.get("block") or {}):
            codes.update(d.keys())
    ALL_COUNTRIES = codes
    return ALL_COUNTRIES


EASTERN_HEMISPHERE = None   # computed lazily as ALL - WESTERN

def _eastern_hemisphere() -> set[str]:
    global EASTERN_HEMISPHERE
    if EASTERN_HEMISPHERE is None:
        EASTERN_HEMISPHERE = _load_all_countries() - WESTERN_HEMISPHERE
    return EASTERN_HEMISPHERE


US_STATES_50_PLUS_DC = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","IA","ID","IL",
    "IN","KS","KY","LA","MA","MD","ME","MI","MN","MO","MS","MT","NC","ND",
    "NE","NH","NJ","NM","NV","NY","OH","OK","OR","PA","RI","SC","SD","TN",
    "TX","UT","VA","VT","WA","WI","WV","WY","DC",
}


# ---------------------------------------------------------------------------
# IO

def load_decisions() -> dict:
    if DECISIONS_FILE.exists():
        return json.loads(DECISIONS_FILE.read_text(encoding="utf-8"))
    return {"decisions": {}}


def save_decisions(data: dict) -> None:
    DATA_OUT.mkdir(parents=True, exist_ok=True)
    DECISIONS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Round 1
# (2026-05-31, user request)
# 1. Reject all suggestions that would block domestic dogs (allowed everywhere).
# 2. Reject all suggestions that would block domestic chickens (allowed everywhere).
# 3. African wildcat: allow throughout Africa + Asia (incl. Turkey); block elsewhere.

DOG_RULE_NAME      = "domestic-dog-allowed-everywhere"
CHICKEN_RULE_NAME  = "domestic-chicken-allowed-everywhere"
WILDCAT_RULE_NAME  = "african-wildcat-africa-asia-only"

DOG_NOTE      = ("Bulk: rejecting -- domestic dog should remain allowed in every region.")
CHICKEN_NOTE  = ("Bulk: rejecting -- domestic chicken should remain allowed in every region.")
WILDCAT_OUT_OF_RANGE_NOTE = (
    "Bulk: this region is out of the African wildcat's range (Africa + Asia, incl. Turkey, Russia); "
    "accepting the proposal (block at species level).")
WILDCAT_IN_RANGE_NOTE = (
    "Bulk: this region is inside the African wildcat's range (Africa + Asia, incl. Turkey, Russia); "
    "rejecting the proposal.")
WILDCAT_SYSTEMATIC_NOTE_TMPL = (
    "Block African wildcat at species level in {n} countries outside Africa + Asia "
    "(incl. Turkey, Russia). Russia treated as Asia (per user override). "
    "Caucasus (ARM/AZE/GEO), Cyprus, Hong Kong/Macau/Taiwan treated as Asia. "
    "Mayotte, Réunion, St Helena, Madagascar, Seychelles, Mauritius, Comoros, "
    "Cape Verde, São Tomé, British Indian Ocean Territory treated as Africa.")


def _wildcat_systematic_decision(entry: dict, now: str) -> dict:
    """Build a custom decision for the African wildcat systematic item."""
    all_countries = set(entry["keepCountries"]) | set(entry["removeCountries"])
    block_countries = sorted(cc for cc in all_countries if cc not in AFRICA_ASIA)
    binom    = entry["binomial"]
    taxon_key = entry["fullTaxonKey"]
    custom = {
        "description": "Block African wildcat outside Africa + Asia (incl. Turkey, Russia).",
        "allowRules":  [],
        "blockRules":  [
            {"taxonLevel": "species",
             "binomial":   binom,
             "taxonKey":   taxon_key,
             "country":    cc,
             "state":      None}
            for cc in block_countries
        ],
    }
    return {
        "outcome":      "custom",
        "custom":       custom,
        "notes":        WILDCAT_SYSTEMATIC_NOTE_TMPL.format(n=len(block_countries)),
        "bulkRound":    1,
        "bulkRuleName": WILDCAT_RULE_NAME,
        "updatedAt":    now,
    }


def _stamp_common_names(queue: list[dict], decisions: dict) -> int:
    """Add/refresh ``commonName`` on every decision entry, looked up from the
    queue.  Returns the number of entries that were changed."""
    id_to_common = {e["id"]: (e.get("commonName") or "") for e in queue}
    changed = 0
    for did, d in decisions.items():
        cn = id_to_common.get(did, "")
        if cn and d.get("commonName") != cn:
            d["commonName"] = cn
            changed += 1
    return changed


def _should_apply(existing: dict | None, rule_name: str) -> bool:
    """Apply iff there's no existing decision, or the existing one came from
    the same bulk rule (so re-applying just refreshes its notes/timestamp).

    Never overwrite a decision the user typed manually (no ``bulkRound``) and
    never silently overwrite a decision set by a different bulk rule.
    """
    if existing is None:
        return True
    if existing.get("bulkRuleName") == rule_name:
        return True
    return False


def _allowed_everywhere_decision(common_name: str, rule_name: str, round_num: int, now: str) -> dict:
    """Build a 'reject (allowed everywhere)' decision for one queue entry."""
    return {
        "outcome":      "reject",
        "notes":        f'Bulk: rejecting -- {common_name} should remain allowed in every region.',
        "bulkRound":    round_num,
        "bulkRuleName": rule_name,
        "updatedAt":    now,
    }


def apply_allowed_everywhere(queue: list[dict],
                             decisions: dict,
                             specs: list[dict],
                             round_num: int,
                             now: str,
                             skipped: list[dict] | None = None) -> dict[str, int]:
    """Apply a bunch of "<species> allowed everywhere -> reject" rules.

    Each spec is {'binomial', 'common', 'rule'}.
    """
    by_bin = {s["binomial"]: s for s in specs}
    by_cn  = {s["common"]:   s for s in specs}
    counts: dict[str, int] = {s["common"]: 0 for s in specs}
    for e in queue:
        bin_ = (e.get("binomial") or "").lower()
        cn   = (e.get("commonName") or "").lower()
        spec = by_bin.get(bin_) or by_cn.get(cn)
        if not spec:
            continue
        existing = decisions.get(e["id"])
        if not _should_apply(existing, spec["rule"]):
            if skipped is not None:
                skipped.append({
                    "id":            e["id"],
                    "rule":          spec["rule"],
                    "existingRule":  (existing or {}).get("bulkRuleName"),
                    "existingNotes": (existing or {}).get("notes"),
                })
            continue
        decisions[e["id"]] = _allowed_everywhere_decision(
            spec["common"], spec["rule"], round_num, now)
        counts[spec["common"]] += 1
    return counts


# Round 2 (user request, 2026-06-01):
# Six additional domestic species, treat as "allowed everywhere".
ROUND_2_SPECS: list[dict] = [
    {"binomial": "anser anser domesticus", "common": "domestic goose",
     "rule": "domestic-goose-allowed-everywhere"},
    {"binomial": "capra aegagrus hircus",  "common": "domestic goat",
     "rule": "domestic-goat-allowed-everywhere"},
    {"binomial": "bos taurus",             "common": "domestic cattle",
     "rule": "domestic-cattle-allowed-everywhere"},
    {"binomial": "equus asinus",           "common": "domestic donkey",
     "rule": "domestic-donkey-allowed-everywhere"},
    {"binomial": "equus mulus",            "common": "domestic mule",
     "rule": "domestic-mule-allowed-everywhere"},
    {"binomial": "sus scrofa scrofa",      "common": "domestic pig",
     "rule": "domestic-pig-allowed-everywhere"},
]


def apply_round_2(queue: list[dict], decisions: dict, now: str,
                  skipped: list[dict] | None = None) -> dict[str, int]:
    return apply_allowed_everywhere(queue, decisions, ROUND_2_SPECS, 2, now, skipped)


# ---------------------------------------------------------------------------
# Round 3 (user request, 2026-06-01):
# 32 species-specific custom rules covering systematic proposals.  Each item
# contributes block rules at species level and (optionally) genus / family.

def _species_block_rule(entry: dict, country: str, state: str | None = None) -> dict:
    return {
        "taxonLevel": "species",
        "binomial":   entry["binomial"],
        "taxonKey":   entry["fullTaxonKey"],
        "country":    country,
        "state":      state,
    }


def _genus_block_rule(entry: dict, genus: str, country: str) -> dict:
    lin = entry["lineage"] or {}
    key = f"{lin.get('class','')};{lin.get('order','')};{lin.get('family','')};{genus};"
    return {
        "taxonLevel": "genus",
        "genus":      genus,
        "taxonKey":   key,
        "country":    country,
        "state":      None,
    }


def _family_block_rule(entry: dict, family: str, country: str) -> dict:
    lin = entry["lineage"] or {}
    key = f"{lin.get('class','')};{lin.get('order','')};{family};;"
    return {
        "taxonLevel": "family",
        "family":     family,
        "taxonKey":   key,
        "country":    country,
        "state":      None,
    }


def _build_custom_decision(entry: dict, item: dict, now: str) -> dict:
    all_countries = _load_all_countries()

    block_rules: list[dict] = []

    species_keep    = item.get("species_keep", set())
    species_block_countries = sorted(all_countries - species_keep)
    for cc in species_block_countries:
        block_rules.append(_species_block_rule(entry, cc))

    # Optional state-level species blocks (yellow-billed magpie case)
    keep_states = item.get("species_keep_us_states")
    if keep_states is not None:
        block_states = sorted(US_STATES_50_PLUS_DC - keep_states)
        for st in block_states:
            block_rules.append(_species_block_rule(entry, "USA", state=st))

    if item.get("genus"):
        genus_keep = item.get("genus_keep", set())
        for cc in sorted(all_countries - genus_keep):
            block_rules.append(_genus_block_rule(entry, item["genus"], cc))

    if item.get("family"):
        family_keep = item.get("family_keep", set())
        for cc in sorted(all_countries - family_keep):
            block_rules.append(_family_block_rule(entry, item["family"], cc))

    return {
        "outcome": "custom",
        "custom": {
            "description": item["notes"],
            "allowRules":  [],
            "blockRules":  block_rules,
        },
        "notes":        item["notes"],
        "bulkRound":    3,
        "bulkRuleName": item["rule_name"],
        "updatedAt":    now,
    }


# Country code shortcuts used in the items list, for readability.
PHL = {"PHL"}
USA_SET = {"USA"}
IDN_SET = {"IDN"}
AUS_SET = {"AUS"}
BRA_BOL = {"BRA", "BOL"}
BRA_BOL_PER_COL = {"BRA","BOL","PER","COL"}
WESTERN_SCREECH_OWL_KEEP = {"CAN","USA","MEX","GTM","SLV","HND","NIC","CRI"}
CHINESE_GORAL_KEEP       = {"CHN","MMR","IND","THA","VNM","LAO"}
CHINESE_SEROW_KEEP       = {"CHN","MMR","THA","VNM","LAO","KHM"}
EASTERN_LAUGHINGTHRUSH_KEEP = {"CHN","MMR","IND","THA","VNM","LAO"}
MOONRAT_KEEP             = {"MYS","MMR","THA","IDN"}
TARSIIDAE_KEEP           = {"BRN","IDN","MYS","PHL"}
STREAKED_WREN_BABBLER_KEEP = {"BGD","KHM","CHN","IND","LAO","MYS","MMR","THA","VNM"}
NORTHERN_HARRIER_KEEP    = {"CAN","USA","MEX","COL","VEN"} | CARIBBEAN


def _round_3_items() -> list[dict]:
    AS_W = _eastern_hemisphere   # placeholder; computed lazily
    items: list[dict] = [
        # ashy-headed babbler
        {"rank": 2, "binomial": "trichastoma cinereiceps",
         "rule_name": "ashy-headed-babbler",
         "species_keep": PHL,
         "genus": "trichastoma",  "genus_keep":  ASIA,
         "family": "pellorneidae","family_keep": ASIA | AFRICA,
         "notes": "Block the ashy-headed babbler in every country other than the Philippines. "
                  "Block the genus trichastoma in every country not in Asia. "
                  "Block the family Pellorneidae in every country not in Asia or Africa."},

        # brazilian squirrel
        {"rank": 3, "binomial": "guerlinguetus brasiliensis",
         "rule_name": "brazilian-squirrel",
         "species_keep": SOUTH_AMERICA | CENTRAL_AMERICA | {"MEX"},
         "genus": "guerlinguetus", "genus_keep": SOUTH_AMERICA | CENTRAL_AMERICA | {"MEX"},
         "notes": "Block the brazilian squirrel in every country outside of South America, Central America, and Mexico. "
                  "Block the genus guerlinguetus in every country outside of South America, Central America, and Mexico."},

        # common woolly monkey
        {"rank": 4, "binomial": "lagothrix lagotricha",
         "rule_name": "common-woolly-monkey",
         "species_keep": SOUTH_AMERICA | CENTRAL_AMERICA | {"MEX"},
         "genus": "lagothrix",  "genus_keep":  SOUTH_AMERICA | CENTRAL_AMERICA | {"MEX"},
         "family": "atelidae",  "family_keep": SOUTH_AMERICA | CENTRAL_AMERICA | {"MEX"},
         "notes": "Block the common woolly monkey in every country outside of South America, Central America, and Mexico. "
                  "Block the genus lagothrix in every country outside of South America, Central America, and Mexico. "
                  "Block the family atelidae in every country outside of South America, Central America, and Mexico."},

        # eastern woodrat
        {"rank": 9, "binomial": "neotoma floridana",
         "rule_name": "eastern-woodrat",
         "species_keep": USA_SET,
         "genus": "neotoma", "genus_keep": USA_SET,
         "notes": "Block the eastern woodrat in every country other than the United States. "
                  "Block the genus neotoma in every country other than the United States."},

        # limestone wren-babbler  (user typo: "limestone wren-warbler")
        {"rank": 11, "binomial": "turdinus crispifrons",
         "rule_name": "limestone-wren-babbler",
         "species_keep": ASIA_NO_RUS,
         "genus": "turdinus", "genus_keep": ASIA_NO_RUS,
         "notes": "Block the limestone wren-babbler in every country outside of Asia "
                  "(don't include Russia as part of Asia for this decision, "
                  "i.e., block this species in Russia). "
                  "Block the genus Turdinus in every country outside of Asia "
                  "(don't include Russia as part of Asia for this decision, "
                  "i.e., block this species in Russia)."},

        # western screech-owl (current GUID)
        {"rank": 49, "binomial": "otus kennicottii",
         "rule_name": "western-screech-owl",
         "species_keep": WESTERN_SCREECH_OWL_KEEP,
         "notes": "Block the western screech-owl in every country other than Canada, "
                  "the United States, Mexico, Guatemala, El Salvador, Honduras, Nicaragua, "
                  "and Costa Rica."},

        # white-vented shama
        {"rank": 13, "binomial": "kittacincla nigra",
         "rule_name": "white-vented-shama",
         "species_keep": PHL,
         "genus": "kittacincla", "genus_keep": ASIA_NO_RUS,
         "notes": "Block the White-vented shama in every country other than the Philippines. "
                  "Block the genus kittacincla in every country not in Asia "
                  "(in this case, 'Asia' doesn't include Russia, i.e., block in Russia)."},

        # yellow-billed magpie (USA / state CA only)
        {"rank": 14, "binomial": "pica nutalli",
         "rule_name": "yellow-billed-magpie",
         "species_keep": USA_SET,
         "species_keep_us_states": {"CA"},
         "notes": "Block the yellow-billed magpie in every country other than the United States, "
                  "and in every US state other than California."},

        # archer's robin-chat
        {"rank": 15, "binomial": "dessonornis archeri",
         "rule_name": "archers-robin-chat",
         "species_keep": AFRICA,
         "genus": "dessonornis", "genus_keep": AFRICA,
         "notes": "Block the archer's robin-chat in every country not in Africa. "
                  "Block the genus Dessonornis in every country not in Africa."},

        # black-eared ground-thrush
        {"rank": 16, "binomial": "geokichla camaronensis",
         "rule_name": "black-eared-ground-thrush",
         "species_keep": AFRICA,
         "genus": "geokichla",
         # genus rule: block in Western hemisphere -> keep is Eastern hemisphere
         "genus_keep_fn": "EASTERN_HEMISPHERE",
         "notes": "Block the Black-eared Ground-Thrush in every country not in Africa. "
                  "Block the genus geokichla in every country in the western hemisphere."},

        # black-winged trumpeter
        {"rank": 17, "binomial": "psophia obscura",
         "rule_name": "black-winged-trumpeter",
         "species_keep": BRA_BOL,
         "genus": "psophia",   "genus_keep":  SOUTH_AMERICA,
         "family": "psophiidae","family_keep": SOUTH_AMERICA,
         "notes": "Block the black-winged trumpeter in every country other than Brazil and Bolivia. "
                  "Block the genus Psophia in every country not in South America. "
                  "Block the family Psophiidae in every country not in South America."},

        # bullock's oriole
        {"rank": 18, "binomial": "icterus bullockiorum",
         "rule_name": "bullocks-oriole",
         "species_keep": NORTH_AMERICA | CENTRAL_AMERICA,
         "genus": "icterus",   "genus_keep_fn": "WESTERN_HEMISPHERE",
         "family": "icteridae","family_keep_fn": "WESTERN_HEMISPHERE",
         "notes": "Block the bullock's oriole in every country not in North America or Central America. "
                  "Block the genus icterus in every country in the Eastern hemisphere. "
                  "Block the family icteridae in every country in the Eastern hemisphere."},

        # chinese goral
        {"rank": 19, "binomial": "naemorhedus griseus",
         "rule_name": "chinese-goral",
         "species_keep": CHINESE_GORAL_KEEP,
         "genus": "naemorhedus", "genus_keep": ASIA,    # ASIA already includes Russia
         "notes": "Block the Chinese goral in every country other than China, Myanmar, "
                  "India, Thailand, Vietnam, and Laos. "
                  "Block the genus naemorhedus in every country not in Asia "
                  "(including Russia, i.e., don't block this species in Russia)."},

        # chinese serow
        {"rank": 20, "binomial": "capricornis milneedwardsii",
         "rule_name": "chinese-serow",
         "species_keep": CHINESE_SEROW_KEEP,
         "genus": "capricornis", "genus_keep": ASIA,
         "notes": "Block the Chinese serow in every country other than China, Myanmar, "
                  "Thailand, Vietnam, Laos, and Cambodia. "
                  "Block the genus capricornis in every country not in Asia."},

        # grey-necked wood-rail
        {"rank": 21, "binomial": "aramides cajaneus",
         "rule_name": "grey-necked-wood-rail",
         "species_keep": CENTRAL_AMERICA | SOUTH_AMERICA,
         "genus": "aramides",  "genus_keep": CENTRAL_AMERICA | SOUTH_AMERICA,
         "notes": "Block the grey-necked wood-rail in every country not in Central America or South America. "
                  "Block the genus aramides in every country not in Central America or South America."},

        # horsfield's tarsier
        {"rank": 22, "binomial": "tarsius bancanus",
         "rule_name": "horsfields-tarsier",
         "species_keep": IDN_SET,
         "genus": "tarsius", "genus_keep": IDN_SET,
         "family": "tarsiidae", "family_keep": TARSIIDAE_KEEP,
         "notes": "Block the horsfield's tarsier in every country other than Indonesia. "
                  "Block the genus tarsius in every country other than Indonesia. "
                  "Block the family tarsiidae in every country other than Brunei, Indonesia, Malaysia, and the Philippines."},

        # long-nosed mongoose
        {"rank": 23, "binomial": "herpestes naso",
         "rule_name": "long-nosed-mongoose",
         "species_keep": AFRICA,
         "genus": "herpestes",   "genus_keep_fn":  "EASTERN_HEMISPHERE",
         "family": "herpestidae","family_keep_fn": "EASTERN_HEMISPHERE",
         "notes": "Block the long-nosed mongoose in every country not in Africa. "
                  "Block the genus herpestes in every country in the Western hemisphere. "
                  "Block the family herpestidae in every country in the Western hemisphere."},

        # ochre-winged trumpeter
        {"rank": 24, "binomial": "psophia ochroptera",
         "rule_name": "ochre-winged-trumpeter",
         "species_keep": BRA_BOL_PER_COL,
         "notes": "Block the ochre-winged trumpeter in every country other than Brazil, Bolivia, Peru, and Colombia."},

        # olive-winged trumpeter
        {"rank": 25, "binomial": "psophia dextralis",
         "rule_name": "olive-winged-trumpeter",
         "species_keep": BRA_BOL_PER_COL,
         "notes": "Block the olive-winged trumpeter in every country other than Brazil, Bolivia, Peru, and Colombia."},

        # purús red howler monkey
        {"rank": 26, "binomial": "alouatta puruensis",
         "rule_name": "purus-red-howler-monkey",
         "species_keep": BRA_BOL_PER_COL,
         "notes": "Block the purús red howler monkey in every country other than Brazil, Bolivia, Peru, and Colombia."},

        # streaked tuftedcheek
        {"rank": 27, "binomial": "pseudocolaptes boissonneauii",
         "rule_name": "streaked-tuftedcheek",
         "species_keep": SOUTH_AMERICA,
         "genus": "pseudocolaptes", "genus_keep": SOUTH_AMERICA,
         "family": "furnariidae",   "family_keep": SOUTH_AMERICA | CENTRAL_AMERICA | {"MEX"},
         "notes": "Block the streaked tuftedcheek in every country not in South America. "
                  "Block the genus pseudocolaptes in every country not in South America. "
                  "Block the family furnariidae in every country outside of South America, Central America, and Mexico."},

        # australian ibis
        {"rank": 28, "binomial": "threskiornis moluccus",
         "rule_name": "australian-ibis",
         "species_keep": AUS_SET,
         "genus": "threskiornis", "genus_keep_fn": "EASTERN_HEMISPHERE",
         "notes": "Block the australian ibis in every country other than Australia. "
                  "Block the genus Threskiornis in every country in the Western hemisphere."},

        # eastern moustached laughingthrush
        {"rank": 29, "binomial": "garrulax cinereiceps",
         "rule_name": "eastern-moustached-laughingthrush",
         "species_keep": EASTERN_LAUGHINGTHRUSH_KEEP,
         "genus": "garrulax",       "genus_keep":  ASIA,
         "family": "leiotrichidae", "family_keep_fn": "EASTERN_HEMISPHERE",
         "notes": "Block the eastern moustached laughingthrush in every country other than China, Myanmar, India, Thailand, Vietnam, and Laos. "
                  "Block the genus Garrulax in every country not in Asia. "
                  "Block the family leiotrichidae in every country in the western hemisphere."},

        # moonrat
        {"rank": 30, "binomial": "echinosorex gymnura",
         "rule_name": "moonrat",
         "species_keep": MOONRAT_KEEP,
         "genus": "echinosorex",  "genus_keep": MOONRAT_KEEP,
         "family": "erinaceidae", "family_keep_fn": "EASTERN_HEMISPHERE",
         "notes": "Block the moonrat in every country other than Malaysia, Myanmar, Thailand, and Indonesia. "
                  "Block the genus echinosorex in every country other than Malaysia, Myanmar, Thailand, and Indonesia. "
                  "Block the family erinaceidae in every country in the western hemisphere."},

        # streaked wren-babbler
        {"rank": 31, "binomial": "turdinus brevicaudatus",
         "rule_name": "streaked-wren-babbler",
         "species_keep": STREAKED_WREN_BABBLER_KEEP,
         "notes": "Block the streaked wren-babbler in every country other than Bangladesh, Cambodia, China, India, Laos, Malaysia, Myanmar, Thailand, and Vietnam."},

        # saddleback tamarin
        {"rank": 33, "binomial": "leontocebus fuscicollis",
         "rule_name": "saddleback-tamarin",
         "species_keep": SOUTH_AMERICA,
         "genus": "leontocebus",    "genus_keep": SOUTH_AMERICA,
         "family": "callitrichidae","family_keep": SOUTH_AMERICA | CENTRAL_AMERICA,
         "notes": "Block the saddleback tamarin in every country not in South America. "
                  "Block the genus leontocebus in every country not in South America. "
                  "Block the family callitrichidae in every country not in South America or Central America."},

        # white/crandall's saddleback tamarin
        {"rank": 34, "binomial": "saguinus melanoleucus",
         "rule_name": "white-crandalls-saddleback-tamarin",
         "species_keep": SOUTH_AMERICA,
         "genus": "saguinus", "genus_keep": SOUTH_AMERICA,
         "notes": "Block the white/crandall's saddleback tamarin in every country not in South America. "
                  "Block the genus saguinus in every country not in South America."},

        # sumatran mountain muntjac
        {"rank": 35, "binomial": "muntiacus montanus",
         "rule_name": "sumatran-mountain-muntjac",
         "species_keep": IDN_SET,
         "genus": "muntiacus", "genus_keep": ASIA,
         "notes": "Block the sumatran mountain muntjac in every country other than Indonesia. "
                  "Block the genus muntiacus in every country not in Asia."},

        # helmeted guineafowl (current GUID = rank 52, override user's "repeat proposal" reject)
        {"rank": 52, "binomial": "numida meleagris",
         "rule_name": "helmeted-guineafowl",
         "species_keep": AFRICA,
         "genus": "numida",     "genus_keep": AFRICA,
         "family": "numididae", "family_keep": AFRICA,
         "force_overwrite_manual": True,
         "notes": "Block the helmeted guineafowl in every country not in Africa. "
                  "Block the genus numida in every country not in Africa. "
                  "Block the family numididae in every country not in Africa."},

        # asian badger
        {"rank": 44, "binomial": "meles leucurus",
         "rule_name": "asian-badger",
         "species_keep": ASIA,
         "genus": "meles", "genus_keep": ASIA | EUROPE,
         "notes": "Block the Asian badger in every country not in Asia. "
                  "Block the genus meles in every country not in Asia or Europe."},

        # common gallinule
        {"rank": 45, "binomial": "gallinula galeata",
         "rule_name": "common-gallinule",
         "species_keep_fn": "WESTERN_HEMISPHERE",
         "notes": "Block the common gallinule in every country in the Eastern hemisphere."},

        # northern harrier
        {"rank": 75, "binomial": "circus hudsonius",
         "rule_name": "northern-harrier",
         "species_keep": NORTHERN_HARRIER_KEEP,
         "notes": "Block the northern harrier outside of Canada, the United States, Mexico, Colombia, Venezuela, and the Caribbean."},
    ]
    # Resolve any "*_keep_fn" entries (we couldn't reference _eastern_hemisphere
    # at class-body time because it depends on a lazy load).
    keep_fns = {
        "WESTERN_HEMISPHERE": WESTERN_HEMISPHERE,
        "EASTERN_HEMISPHERE": _eastern_hemisphere(),
    }
    for it in items:
        for field, fn_field in (("species_keep", "species_keep_fn"),
                                ("genus_keep",   "genus_keep_fn"),
                                ("family_keep",  "family_keep_fn")):
            fn_name = it.pop(fn_field, None)
            if fn_name:
                it[field] = keep_fns[fn_name]
    return items


PITTIDAE_NOTE = ("this proposal likely uses an outdated geofence; in the 4.0.2a "
                 "geofence, pittidae has been blocked in most regions where it "
                 "doesn't belong")


# ---------------------------------------------------------------------------
# Round 5 (user request, 2026-06-01):
# Per-species custom decisions anchored on USA / Canada country-pack
# proposals.  Some have block rules at species/genus/family level; some are
# allow rules at country + state level.

def _species_allow_rule(entry: dict, country: str, state: str | None = None) -> dict:
    return {
        "taxonLevel": "species",
        "binomial":   entry["binomial"],
        "taxonKey":   entry["fullTaxonKey"],
        "country":    country,
        "state":      state,
    }


def _find_entry(queue: list[dict], source: str, kind: str, binomial: str) -> dict | None:
    for e in queue:
        if e["source"] == source and e["kind"] == kind and e.get("binomial") == binomial:
            return e
    return None


def _build_round5_decision(entry: dict, item: dict, now: str) -> dict:
    rule_name = item["rule_name"]
    notes     = item["notes"]
    allow_rules: list[dict] = []
    block_rules: list[dict] = []

    if item.get("kind_in") == "allow":
        for cc in sorted(item.get("allow_countries", set())):
            allow_rules.append(_species_allow_rule(entry, cc))
        for st in sorted(item.get("allow_us_states", set())):
            allow_rules.append(_species_allow_rule(entry, "USA", state=st))
    else:
        sp_block = item.get("species_block", set())
        for cc in sorted(sp_block):
            block_rules.append(_species_block_rule(entry, cc))

        if item.get("genus"):
            for cc in sorted(item.get("genus_block", set())):
                block_rules.append(_genus_block_rule(entry, item["genus"], cc))

        if item.get("family"):
            for cc in sorted(item.get("family_block", set())):
                block_rules.append(_family_block_rule(entry, item["family"], cc))

    return {
        "outcome":      "custom",
        "custom":       {"description": notes,
                         "allowRules":  allow_rules,
                         "blockRules":  block_rules},
        "notes":        notes,
        "bulkRound":    5,
        "bulkRuleName": rule_name,
        "updatedAt":    now,
    }


def _round_5_items() -> list[dict]:
    all_countries = _load_all_countries()
    eastern_hem   = _eastern_hemisphere()
    not_africa    = all_countries - AFRICA
    not_asia      = all_countries - ASIA
    not_asia_or_europe = all_countries - ASIA - EUROPE
    not_aus_or_idn = all_countries - {"AUS", "IDN"}
    not_aus        = all_countries - {"AUS"}
    not_idn_mys_tha = all_countries - {"IDN", "MYS", "THA"}

    return [
        # --- ALLOW (ADD proposals)
        {"source": "canada", "kind": "add", "binomial": "rattus norvegicus",
         "rule_name": "brown-rat",
         "kind_in": "allow",
         "allow_countries": all_countries - {"USA"},
         "allow_us_states": US_STATES_50_PLUS_DC,
         "notes": "Allow the brown rat in every country in the world. In this case, "
                  "specifically create allow rules for every US state."},

        {"source": "canada", "kind": "add", "binomial": "mus musculus",
         "rule_name": "house-mouse",
         "kind_in": "allow",
         "allow_countries": {"CAN"},
         "allow_us_states": US_STATES_50_PLUS_DC,
         "notes": "Allow the house mouse in Canada and in every US state "
                  "(specifically create an allow rule for every US state)."},

        {"source": "usa", "kind": "add", "binomial": "pitangus sulphuratus",
         "rule_name": "great-kiskadee",
         "kind_in": "allow",
         "allow_countries": set(),
         "allow_us_states": {"TX"},
         "notes": "Allow the great kiskadee in Texas, but in no other US states."},

        {"source": "usa", "kind": "add", "binomial": "leopardus pardalis",
         "rule_name": "ocelot",
         "kind_in": "allow",
         "allow_countries": set(),
         "allow_us_states": {"TX"},
         "notes": "Allow the ocelot in Texas, but in no other US states."},

        # --- BLOCK (REMOVE proposals)
        {"source": "canada", "kind": "remove", "binomial": "cyanocorax colliei",
         "rule_name": "black-throated-magpie-jay",
         "species_block": {"CAN"},
         "genus": "cyanocorax", "genus_block": {"CAN"},
         "notes": "Block the black-throated magpie-jay in Canada. "
                  "Block the genus cyanocorax in Canada."},

        {"source": "canada", "kind": "remove", "binomial": "taeniopygia castanotis",
         "rule_name": "australian-zebra-finch",
         "species_block": not_aus,
         "genus": "taeniopygia", "genus_block": not_aus_or_idn,
         "family": "estrildidae", "family_block": WESTERN_HEMISPHERE,
         "notes": "Block the Australian zebra finch in all countries other than Australia. "
                  "Block the genus Taeniopygia in all countries other than Australia and Indonesia. "
                  "Block the family Estrildidae in all countries in the Western hemisphere."},

        {"source": "usa", "kind": "remove", "binomial": "lycalopex griseus",
         "rule_name": "argentine-gray-fox",
         "species_block": {"USA"},
         "genus": "lycalopex", "genus_block": {"USA"},
         "notes": "Block the argentine gray fox in the United States. "
                  "Block the genus lycalopex in the United States."},

        {"source": "usa", "kind": "remove", "binomial": "dendrortyx barbatus",
         "rule_name": "bearded-wood-partridge",
         "species_block": {"USA"},
         "genus": "dendrortyx", "genus_block": {"USA"},
         "notes": "Block the bearded wood-partridge in the United States. "
                  "Block the genus dendrortyx in the United States."},

        {"source": "usa", "kind": "remove", "binomial": "aeromys tephromelas",
         "rule_name": "black-flying-squirrel",
         "species_block": {"USA"},
         "genus": "aeromys", "genus_block": {"USA"},
         "notes": "Block the black flying squirrel in the United States. "
                  "Block the genus aeromys in the United States."},

        {"source": "usa", "kind": "remove", "binomial": "parus minor",
         "rule_name": "eastern-great-tit",
         "species_block": {"USA"},
         "genus": "parus", "genus_block": {"USA"},
         "notes": "Block the eastern great tit in the United States. "
                  "Block the genus parus in the United States."},

        {"source": "usa", "kind": "remove", "binomial": "sapajus macrocephalus",
         "rule_name": "large-headed-capuchin",
         "species_block": {"USA"},
         "genus": "sapajus", "genus_block": {"USA"},
         "family": "cebidae", "family_block": {"USA"},
         "notes": "Block the large-headed capuchin in the United States. "
                  "Block the genus sapajus in the United States. "
                  "Block the family cebidae in the United States."},

        {"source": "usa", "kind": "remove", "binomial": "notocitellus adocetus",
         "rule_name": "lesser-tropical-ground-squirrel",
         "species_block": {"USA"},
         "genus": "notocitellus", "genus_block": {"USA"},
         "notes": "Block the lesser tropical ground squirrel in the United States. "
                  "Block the genus notocitellus in the United States."},

        {"source": "usa", "kind": "remove", "binomial": "ciccaba virgata",
         "rule_name": "mottled-owl",
         "species_block": {"USA"},
         "genus": "ciccaba", "genus_block": {"USA"},
         "notes": "Block the mottled owl in the United States. "
                  "Block the genus ciccaba in the United States."},

        {"source": "usa", "kind": "remove", "binomial": "arremon taciturnus",
         "rule_name": "pectoral-sparrow",
         "species_block": {"USA"},
         "genus": "arremon", "genus_block": {"USA"},
         "notes": "Block the pectoral sparrow in the United States. "
                  "Block the genus arremon in the United States."},

        {"source": "usa", "kind": "remove", "binomial": "funisciurus anerythrus",
         "rule_name": "redness-tree-squirrel",
         "species_block": not_africa,
         "genus": "funisciurus", "genus_block": not_africa,
         "notes": "Block the redness tree squirrel in all countries not in Africa. "
                  "Block the genus funisciurus in all countries not in Africa."},

        {"source": "usa", "kind": "remove", "binomial": "larvivora cyane",
         "rule_name": "siberian-blue-robin",
         "species_block": WESTERN_HEMISPHERE,
         "genus": "larvivora", "genus_block": WESTERN_HEMISPHERE,
         "notes": "Block the siberian blue robin in all countries in the Western hemisphere. "
                  "Block the genus larvivora in all countries in the Western hemisphere."},

        {"source": "usa", "kind": "remove", "binomial": "eutamias sibiricus",
         "rule_name": "siberian-chipmunk",
         "species_block": not_asia_or_europe,
         "genus": "eutamias", "genus_block": not_asia_or_europe,
         "notes": "Block the siberian chipmunk in all countries not in Asia or Europe. "
                  "Block the genus eutamias in all countries not in Asia or Europe."},

        {"source": "usa", "kind": "remove", "binomial": "arundinax aedon",
         "rule_name": "thick-billed-warbler",
         "species_block": {"USA"},
         "genus": "arundinax", "genus_block": {"USA"},
         "notes": "Block the thick-billed warbler in the United States. "
                  "Block the genus arundinax in the United States."},

        {"source": "usa", "kind": "remove", "binomial": "lariscus insignis",
         "rule_name": "three-striped-ground-squirrel",
         "species_block": not_idn_mys_tha,
         "genus": "lariscus", "genus_block": not_asia,
         "notes": "Block the three-striped ground squirrel in all countries other than "
                  "Indonesia, Malaysia, and Thailand. "
                  "Block the genus Lariscus in all countries not in Asia."},

        {"source": "usa", "kind": "remove", "binomial": "callosciurus finlaysonii",
         "rule_name": "variable-squirrel",
         "species_block": not_asia,
         "genus": "callosciurus", "genus_block": not_asia,
         "notes": "Block the variable squirrel in all countries not in Asia. "
                  "Block the genus callosciurus in all countries not in Asia."},
    ]


GRIZZLY_KEEP_STATES = {"AK", "ID", "MT", "WA", "WY"}


def apply_round_9(queue: list[dict], decisions: dict, now: str,
                  skipped: list[dict] | None = None) -> dict[str, int]:
    """Round 9 (2026-06-02): convert every country- or state-level remove
    proposal for the African wildcat to reject; the systematic decision for
    African wildcat is the single source of truth.  Overrides the round-1
    accepts."""
    counts = {"african-wildcat-block-obsolete": 0}
    for e in queue:
        if e.get("binomial") != "felis silvestris lybica":
            continue
        if e["source"] == "systematic":
            continue
        if e["kind"] != "remove":
            continue
        decisions[e["id"]] = {
            "outcome":      "reject",
            "notes":        "made obsolete by a custom decision for a systematic proposal",
            "bulkRound":    9,
            "bulkRuleName": "african-wildcat-block-obsolete",
            "updatedAt":    now,
        }
        counts["african-wildcat-block-obsolete"] += 1
    return counts


def apply_round_8(queue: list[dict], decisions: dict, now: str,
                  skipped: list[dict] | None = None) -> dict[str, int]:
    """Round 8 (2026-06-02):
      * Emperor penguin: custom decision on the Washington DC proposal
        (country-level USA block); reject the other 49 state-level proposals.
      * Grizzly bear: custom decision on the Alabama proposal (5 state-level
        allows + 46 state-level blocks); reject the other 50 state-level
        proposals (including Alaska's manual reject which is overridden).
    """
    counts: dict[str, int] = {}

    def bump(name: str) -> None:
        counts[name] = counts.get(name, 0) + 1

    def find_state_remove(binomial: str, state: str) -> dict | None:
        scope_state = f"USA-{state}"
        for e in queue:
            if (e["source"] == "usa_state" and e["kind"] == "remove"
                    and e.get("binomial") == binomial
                    and e["scope"].get("state") == scope_state):
                return e
        return None

    def apply_custom(entry: dict, rule_name: str, note: str,
                     allow_rules: list[dict], block_rules: list[dict]) -> None:
        existing = decisions.get(entry["id"])
        if not _should_apply(existing, rule_name):
            if skipped is not None:
                skipped.append({"id": entry["id"], "rule": rule_name,
                                "existingRule":  (existing or {}).get("bulkRuleName"),
                                "existingNotes": (existing or {}).get("notes")})
            return
        decisions[entry["id"]] = {
            "outcome": "custom",
            "custom":  {"description": note,
                        "allowRules":  allow_rules,
                        "blockRules":  block_rules},
            "notes":   note,
            "bulkRound":    8,
            "bulkRuleName": rule_name,
            "updatedAt":    now,
        }
        bump(rule_name)

    def reject_others(source: str, kind: str, binomial: str,
                      rule_name: str, note: str,
                      exclude_ids: set[str],
                      force_overwrite_manual: bool = False) -> None:
        for e in queue:
            if e["source"] != source or e["kind"] != kind: continue
            if e.get("binomial") != binomial:               continue
            if e["id"] in exclude_ids:                      continue
            existing = decisions.get(e["id"])
            allow_overwrite = (force_overwrite_manual and existing is not None
                               and existing.get("bulkRound") is None)
            if not _should_apply(existing, rule_name) and not allow_overwrite:
                if skipped is not None:
                    skipped.append({"id": e["id"], "rule": rule_name,
                                    "existingRule":  (existing or {}).get("bulkRuleName"),
                                    "existingNotes": (existing or {}).get("notes")})
                continue
            decisions[e["id"]] = {
                "outcome": "reject", "notes": note,
                "bulkRound": 8, "bulkRuleName": rule_name,
                "updatedAt": now,
            }
            bump(rule_name)

    # --- Emperor penguin (DC anchor)
    ep_dc = find_state_remove("aptenodytes forsteri", "DC")
    if ep_dc is None:
        print("  WARNING: round 8 emperor penguin DC proposal not found")
    else:
        apply_custom(
            ep_dc, "emperor-penguin-anchor-dc",
            "Block the emperor penguin at country level in the United States.",
            allow_rules=[],
            block_rules=[_species_block_rule(ep_dc, "USA")])
        reject_others("usa_state", "remove", "aptenodytes forsteri",
                      "emperor-penguin-other-state-redundant",
                      "handled as a custom decision on another proposal",
                      exclude_ids={ep_dc["id"]})

    # --- Grizzly bear (Alabama anchor)
    gb_al = find_state_remove("ursus u. arctos", "AL")
    if gb_al is None:
        print("  WARNING: round 8 grizzly bear Alabama proposal not found")
    else:
        allow_rules = [_species_allow_rule(gb_al, "USA", state=st)
                       for st in sorted(GRIZZLY_KEEP_STATES)]
        block_rules = [_species_block_rule(gb_al, "USA", state=st)
                       for st in sorted(US_STATES_50_PLUS_DC - GRIZZLY_KEEP_STATES)]
        apply_custom(
            gb_al, "grizzly-bear-anchor-alabama",
            "Allow the grizzly bear in Alaska, Idaho, Montana, Washington, and Wyoming, "
            "and block in all other US states.",
            allow_rules=allow_rules,
            block_rules=block_rules)
        reject_others("usa_state", "remove", "ursus u. arctos",
                      "grizzly-bear-other-state-redundant",
                      "handled as a custom decision on another proposal",
                      exclude_ids={gb_al["id"]},
                      force_overwrite_manual=True)   # Alaska's manual reject

    return counts


def apply_round_7(queue: list[dict], decisions: dict, now: str,
                  skipped: list[dict] | None = None) -> dict[str, int]:
    """Round 7 (2026-06-02):
      * Reject undecided state-level add/remove proposals for several taxa,
        each with its own note.
      * Three "anchor + sweep" rules: mouflon (Hawaii), raccoon dog (Alabama),
        wild goat (California) -- each anchors a custom decision and rejects
        all other state-level removes for that species.
    """
    counts: dict[str, int] = {}
    all_countries = _load_all_countries()

    def bump(name: str) -> None:
        counts[name] = counts.get(name, 0) + 1

    def reject_all(source: str, kind: str, binomial: str,
                   rule_name: str, note: str,
                   exclude_ids: set[str] | None = None) -> None:
        for e in queue:
            if e["source"] != source or e["kind"] != kind: continue
            if e.get("binomial") != binomial:               continue
            if exclude_ids and e["id"] in exclude_ids:      continue
            existing = decisions.get(e["id"])
            if not _should_apply(existing, rule_name):
                if skipped is not None:
                    skipped.append({"id": e["id"], "rule": rule_name,
                                    "existingRule":  (existing or {}).get("bulkRuleName"),
                                    "existingNotes": (existing or {}).get("notes")})
                continue
            decisions[e["id"]] = {
                "outcome": "reject", "notes": note,
                "bulkRound": 7, "bulkRuleName": rule_name,
                "updatedAt": now,
            }
            bump(rule_name)

    def find_state_remove(binomial: str, state: str) -> dict | None:
        scope_state = f"USA-{state}"
        for e in queue:
            if (e["source"] == "usa_state" and e["kind"] == "remove"
                    and e.get("binomial") == binomial
                    and e["scope"].get("state") == scope_state):
                return e
        return None

    def apply_custom(entry: dict, rule_name: str, note: str,
                     block_rules: list[dict]) -> None:
        existing = decisions.get(entry["id"])
        if not _should_apply(existing, rule_name):
            if skipped is not None:
                skipped.append({"id": entry["id"], "rule": rule_name,
                                "existingRule":  (existing or {}).get("bulkRuleName"),
                                "existingNotes": (existing or {}).get("notes")})
            return
        decisions[entry["id"]] = {
            "outcome": "custom",
            "custom":  {"description": note, "allowRules": [],
                        "blockRules": block_rules},
            "notes":   note,
            "bulkRound":    7,
            "bulkRuleName": rule_name,
            "updatedAt":    now,
        }
        bump(rule_name)

    # --- Simple state-level rejects
    reject_all("usa_state", "add",    "mus musculus",
               "house-mouse-state-add-obsolete",
               "made obsolete by a country-level custom decision")
    reject_all("usa_state", "remove", "ovis aries",
               "domestic-sheep-state-block-allowed",
               "domestic species, allowed everywhere")
    reject_all("usa_state", "remove", "homo sapiens",
               "human-state-block-allowed",
               "humans allowed everywhere")
    reject_all("usa_state", "add",    "equus quagga",
               "plains-zebra-state-add",
               "no wild zebra in the US")

    # --- Mouflon anchor on Hawaii + sweep
    mouflon_hi = find_state_remove("ovis orientalis", "HI")
    if mouflon_hi is None:
        print("  WARNING: round 7 mouflon Hawaii proposal not found")
    else:
        block_rules: list[dict] = []
        # Block in all US states
        for st in sorted(US_STATES_50_PLUS_DC):
            block_rules.append(_species_block_rule(mouflon_hi, "USA", state=st))
        # Block in all countries not in Asia or Europe
        for cc in sorted(all_countries - ASIA - EUROPE):
            block_rules.append(_species_block_rule(mouflon_hi, cc))
        apply_custom(
            mouflon_hi, "mouflon-anchor-hawaii",
            "Block mouflon in all US states, and block mouflon in all countries not in Asia or Europe.",
            block_rules)
        reject_all("usa_state", "remove", "ovis orientalis",
                   "mouflon-other-state-redundant",
                   "accepted as a custom decision on another proposal",
                   exclude_ids={mouflon_hi["id"]})

    # --- Raccoon dog anchor on Alabama + sweep
    rd_al = find_state_remove("nyctereutes procyonoides", "AL")
    if rd_al is None:
        print("  WARNING: round 7 raccoon dog Alabama proposal not found")
    else:
        block_rules = [_species_block_rule(rd_al, "USA")]
        apply_custom(
            rd_al, "raccoon-dog-anchor-alabama",
            "Block the raccoon dog at country level in the United States.",
            block_rules)
        reject_all("usa_state", "remove", "nyctereutes procyonoides",
                   "raccoon-dog-other-state-redundant",
                   "accepted as a custom decision on another proposal",
                   exclude_ids={rd_al["id"]})

    # --- Wild goat anchor on California + sweep (state-level only, no country-level)
    wg_ca = find_state_remove("capra aegagrus", "CA")
    if wg_ca is None:
        print("  WARNING: round 7 wild goat California proposal not found")
    else:
        block_rules = []
        for st in sorted(US_STATES_50_PLUS_DC):
            block_rules.append(_species_block_rule(wg_ca, "USA", state=st))
        apply_custom(
            wg_ca, "wild-goat-anchor-california",
            "Block the wild goat with explicit block rules for every US state.",
            block_rules)
        reject_all("usa_state", "remove", "capra aegagrus",
                   "wild-goat-other-state-redundant",
                   "accepted as a custom decision on another proposal",
                   exclude_ids={wg_ca["id"]})

    return counts


def apply_round_6(queue: list[dict], decisions: dict, now: str,
                  skipped: list[dict] | None = None) -> dict[str, int]:
    """Round 6 (2026-06-01):
    Sweep undecided ``usa_state`` remove proposals and reject any whose
    species/genus/family already has a country-level (state=None) USA
    block rule in any prior custom decision.
    """
    counts = {"obsoleted-by-country-block": 0}

    # Index country-level USA blocks across ALL custom decisions.
    usa_country_blocks: dict[tuple, bool] = {}
    for d in decisions.values():
        if d.get("outcome") != "custom":
            continue
        for br in d.get("custom", {}).get("blockRules", []):
            if br.get("country") == "USA" and br.get("state") is None:
                usa_country_blocks[(br["taxonLevel"], br.get("taxonKey"))] = True

    for e in queue:
        if e["source"] != "usa_state":
            continue
        if e["kind"] != "remove":
            continue
        if e["id"] in decisions:
            continue

        lin = e.get("lineage") or {}
        klass      = lin.get("class")  or ""
        order      = lin.get("order")  or ""
        family_raw = lin.get("family") or ""
        genus      = lin.get("genus")  or ""
        species_key = e.get("fullTaxonKey")
        genus_key   = f"{klass};{order};{family_raw};{genus};"
        family_key  = f"{klass};{order};{family_raw};;"

        covered = (
            ("species", species_key) in usa_country_blocks
            or ("genus",   genus_key)   in usa_country_blocks
            or ("family",  family_key)  in usa_country_blocks
        )
        if not covered:
            continue

        decisions[e["id"]] = {
            "outcome":      "reject",
            "notes":        "rendered obsolete by the addition of a country-level block rule",
            "bulkRound":    6,
            "bulkRuleName": "obsoleted-by-country-block",
            "updatedAt":    now,
        }
        counts["obsoleted-by-country-block"] += 1

    return counts


ROUND_5_REJECTS = [
    {"source": "usa", "kind": "remove", "binomial": "taeniopygia castanotis",
     "rule_name": "australian-zebra-finch-usa-redundant",
     "notes": "made redundant by a custom decision for a Canada proposal"},
]


def apply_round_5(queue: list[dict], decisions: dict, now: str,
                  skipped: list[dict] | None = None) -> dict[str, int]:
    counts: dict[str, int] = {}

    for item in _round_5_items():
        entry = _find_entry(queue, item["source"], item["kind"], item["binomial"])
        if entry is None:
            print(f"  WARNING: round 5 item not found: {item['source']}/{item['kind']} {item['binomial']}")
            continue
        existing = decisions.get(entry["id"])
        if not _should_apply(existing, item["rule_name"]):
            if skipped is not None:
                skipped.append({"id": entry["id"], "rule": item["rule_name"],
                                "existingRule": (existing or {}).get("bulkRuleName"),
                                "existingNotes": (existing or {}).get("notes")})
            continue
        decisions[entry["id"]] = _build_round5_decision(entry, item, now)
        counts[item["rule_name"]] = 1

    for r in ROUND_5_REJECTS:
        entry = _find_entry(queue, r["source"], r["kind"], r["binomial"])
        if entry is None:
            print(f"  WARNING: round 5 reject not found: {r['source']}/{r['kind']} {r['binomial']}")
            continue
        existing = decisions.get(entry["id"])
        if not _should_apply(existing, r["rule_name"]):
            if skipped is not None:
                skipped.append({"id": entry["id"], "rule": r["rule_name"],
                                "existingRule": (existing or {}).get("bulkRuleName"),
                                "existingNotes": (existing or {}).get("notes")})
            continue
        decisions[entry["id"]] = {
            "outcome":      "reject",
            "notes":        r["notes"],
            "bulkRound":    5,
            "bulkRuleName": r["rule_name"],
            "updatedAt":    now,
        }
        counts[r["rule_name"]] = 1

    return counts


def apply_round_4(queue: list[dict], decisions: dict, now: str,
                  skipped: list[dict] | None = None) -> dict[str, int]:
    """Round 4 (2026-06-01):
      * Reject undecided regional / state remove proposals that are now
        rendered redundant by a round-3 custom systematic decision (the
        species, genus, or family is already blocked in the same region).
      * Reject any undecided pittidae remove proposals with the user's
        outdated-geofence note.
    """
    counts = {"redundant-with-systematic": 0, "pittidae-outdated": 0}

    # Index round-3 block rules by (level, taxonKey, country, state).
    id_to_entry = {e["id"]: e for e in queue}
    r3_index: dict[tuple, tuple[str, str]] = {}
    for did, d in decisions.items():
        if d.get("bulkRound") != 3 or d.get("outcome") != "custom":
            continue
        src_entry = id_to_entry.get(did)
        src_name  = (src_entry or {}).get("commonName") or "?"
        for br in d.get("custom", {}).get("blockRules", []):
            key = (br["taxonLevel"], br.get("taxonKey"),
                   br.get("country"), br.get("state"))
            r3_index.setdefault(key, (d.get("bulkRuleName"), src_name))

    for e in queue:
        if e["source"] not in ("canada", "usa", "usa_state"):
            continue
        if e["kind"] != "remove":
            continue
        if e["id"] in decisions:
            continue        # already decided -- leave alone

        lin    = e.get("lineage") or {}
        family = (lin.get("family") or "").lower()

        # Pittidae -- always reject with the verbatim note.
        if family == "pittidae":
            decisions[e["id"]] = {
                "outcome":      "reject",
                "notes":        PITTIDAE_NOTE,
                "bulkRound":    4,
                "bulkRuleName": "pittidae-outdated",
                "updatedAt":    now,
            }
            counts["pittidae-outdated"] += 1
            continue

        klass      = lin.get("class")  or ""
        order      = lin.get("order")  or ""
        family_raw = lin.get("family") or ""
        genus      = lin.get("genus")  or ""
        species_key = e.get("fullTaxonKey")
        genus_key   = f"{klass};{order};{family_raw};{genus};"
        family_key  = f"{klass};{order};{family_raw};;"

        scope = e["scope"]
        if scope["kind"] == "country":
            country, state = scope["country"], None
        else:
            country = "USA"
            state   = scope["state"].split("-", 1)[1]

        # Prefer most-specific match: species > genus > family;
        # within each level, prefer state-specific then country-wide.
        match_level = None
        match_src   = None
        for level, tk in (("species", species_key),
                          ("genus",   genus_key),
                          ("family",  family_key)):
            for st in ([state, None] if state is not None else [None]):
                src = r3_index.get((level, tk, country, st))
                if src is not None:
                    match_level = level
                    match_src   = src
                    break
            if match_level:
                break

        if match_level is None:
            continue

        _rule_name_src, src_name = match_src
        region_str = e.get("regionDisplay") or country
        decisions[e["id"]] = {
            "outcome": "reject",
            "notes":   (f"Bulk: now redundant -- this proposal is already covered by "
                        f"the custom decision on the systematic proposal for "
                        f"{src_name} ({match_level}-level block in {region_str})."),
            "bulkRound":    4,
            "bulkRuleName": "redundant-with-systematic",
            "updatedAt":    now,
        }
        counts["redundant-with-systematic"] += 1

    return counts


STALE_GUID_REJECTS = [
    {"rank": 12, "rule_name": "western-screech-owl-stale-guid",
     "notes": "Rejecting -- this is a duplicate of the current-GUID western screech-owl "
              "proposal (rank #49); the custom decision is applied to that copy."},
    {"rank": 43, "rule_name": "helmeted-guineafowl-stale-guid",
     "notes": "Rejecting -- this is a duplicate of the current-GUID helmeted guineafowl "
              "proposal (rank #52); the custom decision is applied to that copy."},
]


def apply_round_3(queue: list[dict], decisions: dict, now: str,
                  skipped: list[dict] | None = None) -> dict[str, int]:
    rank_to_entry = {e["rank"]: e for e in queue if e["source"] == "systematic"}
    counts: dict[str, int] = {}

    # Custom decisions
    for item in _round_3_items():
        entry = rank_to_entry.get(item["rank"])
        if entry is None:
            print(f"  WARNING: round 3 item rank #{item['rank']} not found in queue")
            continue
        decision = _build_custom_decision(entry, item, now)
        existing = decisions.get(entry["id"])
        if item.get("force_overwrite_manual") and existing is not None \
                and existing.get("bulkRound") is None:
            # Explicit user request to overwrite a manual decision.
            decisions[entry["id"]] = decision
            counts[item["rule_name"]] = 1
            continue
        if not _should_apply(existing, item["rule_name"]):
            if skipped is not None:
                skipped.append({
                    "id":            entry["id"],
                    "rule":          item["rule_name"],
                    "existingRule":  (existing or {}).get("bulkRuleName"),
                    "existingNotes": (existing or {}).get("notes"),
                })
            continue
        decisions[entry["id"]] = decision
        counts[item["rule_name"]] = 1

    # Stale-GUID rejects
    for r in STALE_GUID_REJECTS:
        entry = rank_to_entry.get(r["rank"])
        if entry is None:
            print(f"  WARNING: stale-GUID reject rank #{r['rank']} not found in queue")
            continue
        existing = decisions.get(entry["id"])
        if not _should_apply(existing, r["rule_name"]):
            if skipped is not None:
                skipped.append({"id": entry["id"], "rule": r["rule_name"],
                                "existingRule": (existing or {}).get("bulkRuleName"),
                                "existingNotes": (existing or {}).get("notes")})
            continue
        decisions[entry["id"]] = {
            "outcome": "reject",
            "notes":   r["notes"],
            "bulkRound": 3,
            "bulkRuleName": r["rule_name"],
            "updatedAt": now,
        }
        counts[r["rule_name"]] = 1

    return counts


def _try_apply(decisions: dict, eid: str, rule_name: str,
               new_decision: dict, skipped: list[dict] | None) -> bool:
    existing = decisions.get(eid)
    if not _should_apply(existing, rule_name):
        if skipped is not None:
            skipped.append({
                "id":            eid,
                "rule":          rule_name,
                "existingRule":  (existing or {}).get("bulkRuleName"),
                "existingNotes": (existing or {}).get("notes"),
            })
        return False
    decisions[eid] = new_decision
    return True


def apply_round_1(queue: list[dict], decisions: dict, now: str,
                  skipped: list[dict] | None = None) -> dict[str, int]:
    counts = {"dog": 0, "chicken": 0, "wildcat": 0}

    def dec(outcome, notes, rule, **extra):
        return {"outcome": outcome, "notes": notes,
                "bulkRound": 1, "bulkRuleName": rule,
                "updatedAt": now, **extra}

    for e in queue:
        bin_ = (e.get("binomial") or "").lower()
        cn   = (e.get("commonName") or "").lower()

        # --- Rule 1: domestic dog
        if bin_ == "canis familiaris" or cn == "domestic dog":
            if _try_apply(decisions, e["id"], DOG_RULE_NAME,
                          dec("reject", DOG_NOTE, DOG_RULE_NAME), skipped):
                counts["dog"] += 1
            continue

        # --- Rule 2: domestic chicken
        if bin_ == "gallus gallus domesticus" or cn == "domestic chicken":
            if _try_apply(decisions, e["id"], CHICKEN_RULE_NAME,
                          dec("reject", CHICKEN_NOTE, CHICKEN_RULE_NAME), skipped):
                counts["chicken"] += 1
            continue

        # --- Rule 3: African wildcat
        if bin_ == "felis silvestris lybica":
            if e["kind"] == "remove":
                scope = e["scope"]
                region_code = scope.get("country") or ""
                if scope["kind"] == "state":
                    region_code = "USA"
                if region_code in AFRICA_ASIA:
                    new = dec("reject", WILDCAT_IN_RANGE_NOTE, WILDCAT_RULE_NAME)
                else:
                    new = dec("accept", WILDCAT_OUT_OF_RANGE_NOTE, WILDCAT_RULE_NAME)
                if _try_apply(decisions, e["id"], WILDCAT_RULE_NAME, new, skipped):
                    counts["wildcat"] += 1
                continue

            if e["source"] == "systematic":
                new = _wildcat_systematic_decision(e, now)
                if _try_apply(decisions, e["id"], WILDCAT_RULE_NAME, new, skipped):
                    counts["wildcat"] += 1

    return counts


# ---------------------------------------------------------------------------
# Main

def main() -> None:
    print("Building queue ...")
    queue, _ = build_queue()
    data = load_decisions()
    decisions = data.setdefault("decisions", {})
    before = len(decisions)
    now = datetime.now(timezone.utc).isoformat()

    skipped: list[dict] = []

    print("\n=== Round 1 ===")
    c1 = apply_round_1(queue, decisions, now, skipped=skipped)
    print(f"  domestic dog:     {c1['dog']} decisions")
    print(f"  domestic chicken: {c1['chicken']} decisions")
    print(f"  african wildcat:  {c1['wildcat']} decisions")

    print("\n=== Round 2 ===")
    c2 = apply_round_2(queue, decisions, now, skipped=skipped)
    for common, n in c2.items():
        print(f"  {common}: {n} decisions")

    print("\n=== Round 3 ===")
    c3 = apply_round_3(queue, decisions, now, skipped=skipped)
    for rule, n in c3.items():
        print(f"  {rule}: {n} decisions")
    print(f"  total round 3 decisions: {sum(c3.values())}")

    print("\n=== Round 4 ===")
    c4 = apply_round_4(queue, decisions, now, skipped=skipped)
    for rule, n in c4.items():
        print(f"  {rule}: {n} decisions")

    print("\n=== Round 5 ===")
    c5 = apply_round_5(queue, decisions, now, skipped=skipped)
    for rule, n in c5.items():
        print(f"  {rule}: {n} decisions")

    print("\n=== Round 6 ===")
    c6 = apply_round_6(queue, decisions, now, skipped=skipped)
    for rule, n in c6.items():
        print(f"  {rule}: {n} decisions")

    print("\n=== Round 7 ===")
    c7 = apply_round_7(queue, decisions, now, skipped=skipped)
    for rule, n in c7.items():
        print(f"  {rule}: {n} decisions")

    print("\n=== Round 8 ===")
    c8 = apply_round_8(queue, decisions, now, skipped=skipped)
    for rule, n in c8.items():
        print(f"  {rule}: {n} decisions")

    print("\n=== Round 9 ===")
    c9 = apply_round_9(queue, decisions, now, skipped=skipped)
    for rule, n in c9.items():
        print(f"  {rule}: {n} decisions")

    if skipped:
        print(f"\nSkipped {len(skipped)} entries with pre-existing decisions:")
        for s in skipped[:20]:
            print(f"  {s['id']}: keeping existing decision "
                  f"(rule={s['existingRule'] or 'manual'})")
        if len(skipped) > 20:
            print(f"  ... and {len(skipped) - 20} more")

    # Stamp commonName onto every decision for human readability.  The field
    # is decorative (no code reads it) but makes the JSON file scannable.
    stamped = _stamp_common_names(queue, decisions)
    if stamped:
        print(f"\nStamped commonName on {stamped} decisions.")

    save_decisions(data)
    after = len(decisions)
    print(f"\nDecisions in file: {before} -> {after}  (delta {after - before})")
    print(f"Wrote: {DECISIONS_FILE}")


if __name__ == "__main__":
    main()
