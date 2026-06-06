"""Simulate fix_geofence_base and flag every allow row that would be
silently dropped because the taxon either isn't in the geofence at all
or has no "allow" key.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from paths import CAMERATRAPAI_DIR  # noqa: E402

BASE = CAMERATRAPAI_DIR / "data" / "geofence_base.json"
DEFAULT_FIXES = CAMERATRAPAI_DIR / "data" / "geofence_fixes.csv"


def main() -> int:
    fixes_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_FIXES
    geofence: dict[str, dict] = json.loads(BASE.read_text(encoding="utf-8"))

    no_entry_hits: list[tuple[int, str, str, str]] = []
    only_block_hits: list[tuple[int, str, str, str]] = []

    with fixes_path.open("r", encoding="utf-8", newline="") as f:
        saw_header = False
        for line_num, row in enumerate(csv.reader(f), start=1):
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
                    no_entry_hits.append((line_num, label, country, state))
                    continue
                if "allow" not in geofence[label]:
                    only_block_hits.append((line_num, label, country, state))
                    continue
                # Apply the allow update (so subsequent rows see it).
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
            elif rule == "block":
                if label not in geofence:
                    geofence[label] = {"block": {country: [state] if state else []}}
                if "block" not in geofence[label]:
                    geofence[label]["block"] = {country: [state] if state else []}
                # (precise block update isn't needed for this check)

    print(f"Allow rows silently dropped because label not in geofence: "
          f"{len(no_entry_hits)}")
    for ln, lab, c, s in no_entry_hits:
        print(f"  line {ln}: {lab},allow,{c},{s}")
    print()
    print(f"Allow rows silently dropped because label had only block: "
          f"{len(only_block_hits)}")
    for ln, lab, c, s in only_block_hits:
        print(f"  line {ln}: {lab},allow,{c},{s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
