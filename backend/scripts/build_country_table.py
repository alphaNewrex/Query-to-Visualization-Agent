"""Rebuild catalog/data/countries.json: each registry country name with its ISO 3166-1 codes.

Run from backend/:  uv run python scripts/build_country_table.py

One request, to GET /stats/field/values?fields=LocationCountry. Names that `pycountry` knows exactly
are mapped by it; the rest come from OVERRIDES. A name that is neither, or an override that is not a
real code (Kosovo's user-assigned XKX is the one exception), fails the build, so the committed table
is verified when it is made. `pycountry` is a development dependency: the service reads only the JSON,
so the alpha-2 codes that users type are written into it too.
"""

import json
import sys
from pathlib import Path
from typing import Final

import httpx2
import pycountry

from ctviz.settings import Settings

OUTPUT: Final = Path(__file__).parent.parent / "src" / "ctviz" / "catalog" / "data" / "countries.json"
USER_AGENT: Final = "ctviz-build-country-table/0.1 (ClinicalTrials.gov query-to-visualization take-home)"

# Registry names that `pycountry` does not match exactly. None marks a value that names no single
# country today: it stays in the table so that it is known, and has no code.
OVERRIDES: Final[dict[str, str | None]] = {
    "Turkey (Türkiye)": "TUR",
    "Russia": "RUS",
    "Democratic Republic of the Congo": "COD",
    "Reunion": "REU",
    "C\u00f4te d\u2019Ivoire": "CIV",
    "The Gambia": "GMB",
    "The Bahamas": "BHS",
    "Palestinian Territories": "PSE",
    "Burma": "MMR",
    "Macau": "MAC",
    "Brunei": "BRN",
    "Holy See": "VAT",
    "Aland Islands": "ALA",
    "Bonaire, Saint Eustatius and Saba": "BES",
    "Curacao": "CUW",
    "French Southern and Antarctic Lands": "ATF",
    "Micronesia": "FSM",
    "Saint Martin": "MAF",
    "Kosovo": "XKX",
    "Virgin Islands": None,
    "Serbia and Montenegro": None,
    "Federal Republic of Yugoslavia": None,
    "Netherlands Antilles": None,
}
# Kosovo has no ISO 3166-1 code; these are the user-assigned ones that the European Union and others use.
USER_ASSIGNED: Final = {"XKX": "XK"}


def registry_countries(stats: object) -> list[str]:
    """The distinct country names of the statistics response, trimmed (one has a trailing space)."""
    if not isinstance(stats, list) or not stats or not isinstance(stats[0], dict):
        raise ValueError("Unexpected statistics response.")
    return sorted({item["value"].strip() for item in stats[0]["topValues"]})


def exact_code(name: str) -> str | None:
    """The alpha-3 code of a country whose name, official name or common name is exactly `name`."""
    found = (
        pycountry.countries.get(name=name)
        or pycountry.countries.get(official_name=name)
        or pycountry.countries.get(common_name=name)
    )
    return None if found is None else str(found.alpha_3)


def build_table(names: list[str]) -> list[dict[str, str | None]]:
    """One row per registry name: `name`, `iso_alpha3` and `iso_alpha2`, the codes null where none exists."""
    table: dict[str, str | None] = {}
    unmapped = []
    for name in names:
        if name in OVERRIDES:
            table[name] = OVERRIDES[name]
        elif (code := exact_code(name)) is not None:
            table[name] = code
        else:
            unmapped.append(name)
    if unmapped:
        raise ValueError(f"No ISO code for: {unmapped}")
    invalid = {
        name: code
        for name, code in table.items()
        if code is not None and code not in USER_ASSIGNED and pycountry.countries.get(alpha_3=code) is None
    }
    if invalid:
        raise ValueError(f"Not ISO 3166-1 alpha-3 codes: {invalid}")
    stale = sorted(set(OVERRIDES) - set(names))
    if stale:
        raise ValueError(f"Overrides for names the registry no longer has: {stale}")
    return [
        {"name": name, "iso_alpha3": code, "iso_alpha2": alpha2(code)} for name, code in sorted(table.items())
    ]


def alpha2(alpha3: str | None) -> str | None:
    if alpha3 is None:
        return None
    if alpha3 in USER_ASSIGNED:
        return USER_ASSIGNED[alpha3]
    return str(pycountry.countries.get(alpha_3=alpha3).alpha_2)  # type: ignore[union-attr]


def main() -> int:
    base_url = Settings.model_construct().ctgov_base_url
    response = httpx2.get(
        f"{base_url}/stats/field/values",
        params={"fields": "LocationCountry"},
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    response.raise_for_status()
    rows = build_table(registry_countries(response.json()))
    OUTPUT.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT} ({len(rows)} names)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
