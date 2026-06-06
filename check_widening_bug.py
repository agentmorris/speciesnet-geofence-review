"""Scan ``geofence_fixes.csv`` for cases where a country-level
``allow``/``block`` row would silently fail to widen an existing
state-restricted entry in ``geofence_base.json`` (or in an earlier row of
the same CSV).

Bug, in ``fix_geofence_base``:

    if not state:
        geofence[label][rule][country] = geofence[label][rule].get(country, [])

If the current value at ``[label][rule][country]`` is a non-empty state
list, ``.get`` returns that list unchanged.  The country-wide fix is
silently a no-op.

This script does not modify anything.  It just walks the CSV in order,
simulating the parts of ``fix_geofence_base`` that affect ``allow``/``block``
country/state lists, and prints every row where the bug fires.
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
    FIXES = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_FIXES
    geofence: dict[str, dict] = json.loads(BASE.read_text(encoding="utf-8"))

    hits: list[tuple[int, str, str, str, list[str]]] = []
    # ^ (line number, label, rule, country, the pre-existing state list)

    with FIXES.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = None
        for row_idx, row in enumerate(reader, start=1):
            if not row:
                continue
            if row[0].startswith("#"):
                continue
            if header is None:
                header = row
                continue
            # expected columns: species,rule,country_code,admin1_region_code
            if len(row) < 4:
                # tolerate trailing-comma rows
                row = row + [""] * (4 - len(row))
            label, rule, country, state = row[0], row[1], row[2], row[3]
            label = label.lower()
            rule = rule.lower()

            # Replicate the part of fix_geofence_base that mutates the
            # in-memory geofence dict (allow + block branches).
            if rule == "allow":
                if label not in geofence:
                    continue
                if "allow" not in geofence[label]:
                    continue
                if not state:
                    current = geofence[label]["allow"].get(country)
                    if current and len(current) > 0:
                        hits.append((row_idx, label, "allow", country, list(current)))
                    geofence[label]["allow"][country] = geofence[label]["allow"].get(
                        country, []
                    )
                else:
                    curr = geofence[label]["allow"].get(country)
                    if curr is None:
                        geofence[label]["allow"][country] = [state]
                    elif not curr:
                        continue
                    else:
                        geofence[label]["allow"][country] = sorted(
                            set(curr) | {state}
                        )
            elif rule == "block":
                if label not in geofence:
                    geofence[label] = {"block": {country: [state] if state else []}}
                if "block" not in geofence[label]:
                    geofence[label]["block"] = {country: [state] if state else []}
                if not state:
                    current = geofence[label]["block"].get(country)
                    if current and len(current) > 0:
                        hits.append((row_idx, label, "block", country, list(current)))
                    geofence[label]["block"][country] = geofence[label]["block"].get(
                        country, []
                    )
                else:
                    curr = geofence[label]["block"].get(country)
                    if curr is None:
                        geofence[label]["block"][country] = [state]
                    elif not curr:
                        continue
                    else:
                        geofence[label]["block"][country] = sorted(
                            set(curr) | {state}
                        )

    print(f"CSV rows scanned: simulated against {BASE.name}")
    print(f"Bug occurrences (country-level row would not widen state-restricted entry): "
          f"{len(hits)}")
    print()
    for row_idx, label, rule, country, existing in hits:
        print(f"  line {row_idx}: {label}  {rule},{country},  "
              f"(existing {rule}.{country} = {existing})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
