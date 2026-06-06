"""One-off: update the capuchin/sapajus/cebidae custom decision so that
the genus and family blocks apply to "all US states except Florida"
rather than country-wide.

Matches the prior anchor convention used elsewhere in this project:
"all US states" means the 50 states + DC, no US territories.  So
"except Florida" means 49 states + DC.

The species-level block on the capuchin itself stays country-wide.
"""

from __future__ import annotations

import json
from pathlib import Path

from paths import DECISIONS_FILE

DECISION_ID = "usa:USA:remove:8e94fb38-c154-45a1-b16d-1a1b10cdfa34"

# 50 US states + DC, minus FL.  Sorted alphabetically.
STATES_EX_FL = sorted({
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DC", "DE", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
})
assert len(STATES_EX_FL) == 50, len(STATES_EX_FL)
assert "FL" not in STATES_EX_FL


def main() -> None:
    data = json.loads(DECISIONS_FILE.read_text(encoding="utf-8"))
    dec = data["decisions"][DECISION_ID]
    assert dec["outcome"] == "custom"

    new_note = (
        "Block the large-headed capuchin in the United States. "
        "Block the genus sapajus in all US states except Florida. "
        "Block the family cebidae in all US states except Florida."
    )

    block_rules: list[dict] = []
    # Species: country-wide block (unchanged).
    block_rules.append({
        "taxonLevel": "species",
        "binomial":   "sapajus macrocephalus",
        "taxonKey":   "mammalia;primates;cebidae;sapajus;macrocephalus",
        "country":    "USA",
        "state":      None,
    })
    # Genus: per-state for every state-and-DC except FL.
    for s in STATES_EX_FL:
        block_rules.append({
            "taxonLevel": "genus",
            "genus":      "sapajus",
            "taxonKey":   "mammalia;primates;cebidae;sapajus;",
            "country":    "USA",
            "state":      s,
        })
    # Family: per-state for every state-and-DC except FL.
    for s in STATES_EX_FL:
        block_rules.append({
            "taxonLevel": "family",
            "family":     "cebidae",
            "taxonKey":   "mammalia;primates;cebidae;;",
            "country":    "USA",
            "state":      s,
        })

    dec["custom"]["description"] = new_note
    dec["custom"]["blockRules"]  = block_rules
    dec["notes"]                 = new_note

    DECISIONS_FILE.write_text(
        json.dumps(data, indent=2), encoding="utf-8"
    )
    print(f"Updated decision {DECISION_ID}")
    print(f"  notes: {new_note}")
    print(f"  total blockRules: {len(block_rules)}")


if __name__ == "__main__":
    main()
