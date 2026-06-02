"""ISO3 country and USA state code -> human-readable name."""

from __future__ import annotations

import pycountry


# Names pycountry returns that are too formal/long for an at-a-glance display.
# Keys are ISO3 alpha-3 codes; values are the display names we prefer.
COUNTRY_NAME_OVERRIDES: dict[str, str] = {
    "RUS": "Russia",
    "USA": "United States",
    "GBR": "United Kingdom",
    "PRK": "North Korea",
    "KOR": "South Korea",
    "IRN": "Iran",
    "SYR": "Syria",
    "LAO": "Laos",
    "BOL": "Bolivia",
    "VEN": "Venezuela",
    "TZA": "Tanzania",
    "MDA": "Moldova",
    "PSE": "Palestine",
    "BES": "Bonaire / St. Eustatius / Saba",
    "SHN": "St. Helena / Ascension",
    "TWN": "Taiwan",
    "VNM": "Vietnam",
    "MKD": "North Macedonia",
    "SVK": "Slovakia",
    "CZE": "Czechia",
    "FSM": "Micronesia",
    "BRN": "Brunei",
    "COD": "DR Congo",
    "COG": "Republic of the Congo",
    "CIV": "Côte d'Ivoire",
    "ALA": "Åland Islands",
    "CUW": "Curaçao",
    "TUR": "Türkiye",
    "MMR": "Myanmar",
    "STP": "São Tomé & Príncipe",
    "ESH": "Western Sahara",
    "ATF": "French Southern Territories",
    "BVT": "Bouvet Island",
    "HMD": "Heard & McDonald Islands",
    "SGS": "South Georgia & South Sandwich Islands",
    "SJM": "Svalbard & Jan Mayen",
    "UMI": "U.S. Minor Outlying Islands",
    "VAT": "Vatican City",
    "VGB": "British Virgin Islands",
    "VIR": "U.S. Virgin Islands",
    "PRI": "Puerto Rico",
    "WLF": "Wallis & Futuna",
    "PCN": "Pitcairn Islands",
    "MAF": "Saint Martin (French)",
    "SXM": "Sint Maarten (Dutch)",
    "BLM": "Saint Barthélemy",
    "TCA": "Turks & Caicos",
    "ATG": "Antigua & Barbuda",
    "KNA": "Saint Kitts & Nevis",
    "VCT": "Saint Vincent & the Grenadines",
    "TTO": "Trinidad & Tobago",
    "STH": "St. Helena",
    "IOT": "British Indian Ocean Territory",
}


def country_name(iso3: str) -> str:
    """Return a short human-readable country name for an ISO3 alpha-3 code.

    Falls back to the input code if pycountry doesn't recognize it.
    """
    if iso3 in COUNTRY_NAME_OVERRIDES:
        return COUNTRY_NAME_OVERRIDES[iso3]
    co = pycountry.countries.get(alpha_3=iso3)
    if co is None:
        return iso3
    return getattr(co, "common_name", None) or co.name


def state_name(usa_state: str) -> str:
    """Convert ``USA-<XX>`` to the full state name (e.g. ``USA-AK`` -> ``Alaska``).

    Returns the input unchanged if it doesn't match the expected pattern.
    """
    if not usa_state.startswith("USA-") or len(usa_state) != 6:
        return usa_state
    code = "US-" + usa_state.split("-", 1)[1]
    s = pycountry.subdivisions.get(code=code)
    return s.name if s else usa_state


def region_display(scope: dict) -> str:
    """Display name for a scope dict ({'kind': ..., 'country'/'state': ...})."""
    if scope["kind"] == "global":
        return "global"
    if scope["kind"] == "country":
        return country_name(scope["country"])
    if scope["kind"] == "state":
        return f"{state_name(scope['state'])} (USA)"
    return ""
