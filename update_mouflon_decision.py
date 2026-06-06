"""One-off: update the mouflon Hawaii-anchor custom decision so that
HI and TX are excluded from the per-state US block list, and the
country-wide USA block is removed.

Resulting intent (matches the new notes):
  - Block mouflon in every US state except HI and TX (49 state-level rules,
    i.e. 50 states + DC minus HI and TX).
  - Block mouflon country-wide in every non-USA country that was already
    in the anchor's block list (146 entries).
  - No country-wide USA block any more (the corresponding USA-remove
    accept has been changed to reject separately).
"""

from __future__ import annotations

import json
from pathlib import Path

from paths import DECISIONS_FILE

DECISION_ID = "usa_state:USA-HI:remove:80921fd4-335d-488e-96f3-cca25b40d5ed"
DROP_USA_STATES = {"HI", "TX"}


def main() -> None:
    data = json.loads(DECISIONS_FILE.read_text(encoding="utf-8"))
    dec = data["decisions"][DECISION_ID]
    assert dec["outcome"] == "custom"

    new_note = (
        "Block mouflon in all US states other than Hawaii and Texas, and "
        "block mouflon in all countries other than the US that are not in "
        "Asia or Europe."
    )

    old_rules = dec["custom"]["blockRules"]
    new_rules: list[dict] = []
    dropped_usa_states: list[str] = []
    dropped_usa_countrywide = 0
    for r in old_rules:
        country = r.get("country")
        state   = r.get("state")
        if country == "USA":
            if state is None:
                dropped_usa_countrywide += 1
                continue
            if state in DROP_USA_STATES:
                dropped_usa_states.append(state)
                continue
        new_rules.append(r)

    dec["custom"]["description"] = new_note
    dec["custom"]["blockRules"]  = new_rules
    dec["notes"]                 = new_note

    DECISIONS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

    print(f"Updated decision {DECISION_ID}")
    print(f"  notes: {new_note}")
    print(f"  blockRules: {len(old_rules)} -> {len(new_rules)}")
    print(f"  dropped USA country-wide:  {dropped_usa_countrywide}")
    print(f"  dropped USA-state entries: {sorted(dropped_usa_states)}")


if __name__ == "__main__":
    main()
