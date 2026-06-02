"""Central place for all filesystem paths used by this project.

Edit DATA_DIR if you set this up on another machine; everything else is
derived from it.  External (cameratrapai) paths are listed at the bottom.
"""

from __future__ import annotations

from pathlib import Path

# Base data folder.  Holds the four suggestion files we review, generated
# reports, and the decisions file.  Adjust this path on a different machine.
DATA_DIR = Path(r"G:\temp\speciesnet-geofence-review-data")
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Suggestion files (inputs).  Keyed by source slug used in queue entry IDs.

SUGGESTION_FILES: dict[str, Path] = {
    "systematic": DATA_DIR / "Systematic_review.json",
    "canada":     DATA_DIR / "Canada_review.json",
    "usa":        DATA_DIR / "USA_review.json",
    "usa_state":  DATA_DIR / "USA_State_review.json",
}

# ---------------------------------------------------------------------------
# Generated files (outputs).

DECISIONS_FILE                     = DATA_DIR / "decisions.json"
VERIFICATION_MISMATCHES_FILE       = DATA_DIR / "verification_mismatches.json"
VERIFICATION_INCONSISTENCIES_FILE  = DATA_DIR / "verification_inconsistencies.json"
VERIFICATION_INCONSISTENCIES_MD    = DATA_DIR / "verification_inconsistencies.md"

# ---------------------------------------------------------------------------
# External inputs from the cameratrapai checkout.  These live outside the
# data folder because they belong to a separate project (SpeciesNet).

CAMERATRAPAI_DIR = Path(r"c:\git\cameratrapai")
TAXONOMY_FILE    = CAMERATRAPAI_DIR / "data" / "model_package" / "taxonomy_release.txt"
LABELS_FILE      = CAMERATRAPAI_DIR / "data" / "model_package" / "always_crop_99710272_22x8_v12_epoch_00148.labels.txt"
GEOFENCE_FILE    = CAMERATRAPAI_DIR / "data" / "model_package" / "geofence_release.json"
