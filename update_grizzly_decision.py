"""One-off: update the grizzly bear custom decision to also block at the
country level in every country where the brown bear (ursus arctos) is
not currently allowed in geofence_base.json.

The 5 USA state-level allow rules and the 46 USA state-level block rules
in the existing decision are preserved.  We add ~197 country-level block
rules so the grizzly subspecies' effective allow set becomes the same as
the brown bear species' (52 countries, with USA further restricted to
the 5 listed states via the existing state-level rules).

Also refreshes the (stale) binomial on the existing block rules to
match the corrected taxonomy name.
"""

from __future__ import annotations

import json
from pathlib import Path

from paths import DECISIONS_FILE
from bulk_apply import _load_all_countries

CAMERATRAPAI_DIR = Path(r"C:/git/cameratrapai")
BASE = CAMERATRAPAI_DIR / "data" / "geofence_base.json"

DECISION_ID = "usa_state:USA-AL:remove:601cf098-9876-4912-84bb-0926834305e9"
TAXON_KEY   = "mammalia;carnivora;ursidae;ursus;arctos horribilis"
BINOMIAL    = "ursus arctos horribilis"

NEW_NOTE = (
    "Allow the grizzly bear in Alaska, Idaho, Montana, Washington, and "
    "Wyoming, and block in all other US states. Also block in all "
    "countries other than Afghanistan, Åland Islands, Albania, Andorra, "
    "Armenia, Austria, Azerbaijan, Belarus, Bosnia and Herzegovina, "
    "Bulgaria, Canada, China, Croatia, Czechia, Estonia, Finland, France, "
    "Georgia, Germany, Greece, Hungary, India, Iran, Iraq, Italy, Japan, "
    "Kazakhstan, Kyrgyzstan, Latvia, Liechtenstein, Mongolia, Montenegro, "
    "North Korea, North Macedonia, Norway, Pakistan, Poland, Romania, "
    "Russia, San Marino, Serbia, Slovakia, Slovenia, Spain, Sweden, "
    "Switzerland, Tajikistan, Turkmenistan, Türkiye, Ukraine, United "
    "States, and Uzbekistan."
)


def main() -> None:
    base = json.loads(BASE.read_text(encoding="utf-8"))
    brown_allowed = set(
        base["mammalia;carnivora;ursidae;ursus;arctos"]["allow"].keys()
    )
    assert len(brown_allowed) == 52, len(brown_allowed)

    universe = _load_all_countries()
    countries_to_block = sorted(universe - brown_allowed)

    data = json.loads(DECISIONS_FILE.read_text(encoding="utf-8"))
    dec = data["decisions"][DECISION_ID]
    assert dec["outcome"] == "custom"

    custom = dec["custom"]
    # Preserve allow rules unchanged (just refresh the binomial).
    for r in custom["allowRules"]:
        r["binomial"] = BINOMIAL

    # Preserve existing USA state-level block rules; refresh binomial.
    existing_state_blocks: list[dict] = []
    for r in custom["blockRules"]:
        if r.get("country") == "USA" and r.get("state"):
            r["binomial"] = BINOMIAL
            existing_state_blocks.append(r)

    # Add new country-level block rules for every country not on the
    # brown bear's allow list.
    new_country_blocks = [
        {
            "taxonLevel": "species",
            "binomial":   BINOMIAL,
            "taxonKey":   TAXON_KEY,
            "country":    cc,
            "state":      None,
        }
        for cc in countries_to_block
    ]

    custom["description"] = NEW_NOTE
    custom["blockRules"]  = existing_state_blocks + new_country_blocks
    dec["notes"]          = NEW_NOTE

    DECISIONS_FILE.write_text(
        json.dumps(data, indent=2), encoding="utf-8"
    )

    print(f"Updated decision {DECISION_ID}")
    print(f"  USA state allow rules:    {len(custom['allowRules'])}")
    print(f"  USA state block rules:    {len(existing_state_blocks)}")
    print(f"  new country block rules:  {len(new_country_blocks)}")
    print(f"  total block rules:        {len(custom['blockRules'])}")
    print(f"  notes: {NEW_NOTE[:80]}...")


if __name__ == "__main__":
    main()
