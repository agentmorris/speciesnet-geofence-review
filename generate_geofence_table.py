"""Generate an exhaustive [taxon, country, admin1, blocked] table for a
given geofence_release.json.

For every taxon in taxonomy_release.txt we query
should_geofence_animal_classification at the following locations:

  * every ISO3 country code from pycountry, with admin1 = None
  * (USA, admin1) for each of the 51 USA admin1 codes (50 states + DC)

We pick the location universe from pycountry (~250 codes) rather than
"codes that appear in either geofence" so the two branches produce
exactly the same row-set and we can diff row-by-row.

Usage:
    python generate_geofence_table.py <geofence_release.json> <out.csv>
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pycountry

sys.path.insert(0, str(Path(r"C:/git/cameratrapai")))
from speciesnet.geofence_utils import (
    should_geofence_animal_classification,
)


TAXONOMY = Path(r"C:/git/cameratrapai/data/model_package/taxonomy_release.txt")

# 50 states + DC (no territories -- those have their own ISO3 code).
USA_ADMIN1 = sorted({
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DC", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
})


def load_taxa(path: Path) -> list[str]:
    """Return every non-empty 7-token line from taxonomy_release.txt."""
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.count(";") != 6:
            continue
        out.append(line)
    return out


def main() -> int:
    geofence_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])

    geofence = json.loads(geofence_path.read_text(encoding="utf-8"))
    taxa = load_taxa(TAXONOMY)
    countries = sorted({c.alpha_3 for c in pycountry.countries})

    print(f"taxa:        {len(taxa)}")
    print(f"countries:   {len(countries)}")
    print(f"USA admin1:  {len(USA_ADMIN1)}")
    queries_per_taxon = len(countries) + len(USA_ADMIN1)
    print(f"queries/tax: {queries_per_taxon}")
    print(f"total:       {len(taxa) * queries_per_taxon}")

    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["taxon_key", "country", "admin1", "blocked"])
        # 5-token taxon for joining with geofence_fixes.csv.
        for i, taxon in enumerate(taxa):
            five_token = ";".join(taxon.split(";")[1:6])
            # Country-level queries.
            for c in countries:
                blocked = should_geofence_animal_classification(
                    taxon, c, None, geofence, enable_geofence=True
                )
                w.writerow([five_token, c, "", int(bool(blocked))])
            # USA admin1 queries.
            for s in USA_ADMIN1:
                blocked = should_geofence_animal_classification(
                    taxon, "USA", s, geofence, enable_geofence=True
                )
                w.writerow([five_token, "USA", s, int(bool(blocked))])
            if (i + 1) % 500 == 0:
                print(f"  ... {i + 1}/{len(taxa)} taxa done")

    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
