"""The country table: registry names, their ISO codes, and the names users call them.

The registry holds 226 English country names, never ISO codes. `resolve` is an exact, case-folded
lookup over those names, a short hand-written alias list and ISO codes, and nothing else. There is no
fuzzy matching: a fuzzy lookup maps Kosovo to Serbia and Curacao to the Netherlands. `suggest` is the
one fuzzy step, and what it returns is only ever offered as a suggestion.
"""

import difflib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Final

COUNTRIES_FILE: Final = Path(__file__).parent / "data" / "countries.json"

# The registry spells it with a typographic apostrophe.
IVORY_COAST: Final = "C\u00f4te d\u2019Ivoire"

# Alias, case-folded, to the registry name it means.
ALIASES: Final = {
    "usa": "United States",
    "us": "United States",
    "u.s.": "United States",
    "u.s.a.": "United States",
    "america": "United States",
    "united states of america": "United States",
    "uk": "United Kingdom",
    "u.k.": "United Kingdom",
    "britain": "United Kingdom",
    "great britain": "United Kingdom",
    "türkiye": "Turkey (Türkiye)",
    "turkiye": "Turkey (Türkiye)",
    "turkey": "Turkey (Türkiye)",
    "russian federation": "Russia",
    "czech republic": "Czechia",
    "ivory coast": IVORY_COAST,
    "cote d'ivoire": IVORY_COAST,
    "côte d'ivoire": IVORY_COAST,
    "republic of korea": "South Korea",
    "korea, republic of": "South Korea",
    "uae": "United Arab Emirates",
    "drc": "Democratic Republic of the Congo",
    "bahamas": "The Bahamas",
    "gambia": "The Gambia",
    "macao": "Macau",
    "myanmar": "Burma",
    "palestine": "Palestinian Territories",
    "swaziland": "Eswatini",
    "cape verde": "Cabo Verde",
    "east timor": "Timor-Leste",
    "vatican": "Holy See",
}
# Words that name more than one country, read as one of them and said so.
ASSUMED_ALIASES: Final = {
    "korea": ("South Korea", "'Korea' is read as South Korea; North Korea is a separate registry value."),
}


@dataclass(frozen=True)
class CountryMatch:
    name: str  # the registry's name, which is what a search and a citation use
    iso_alpha3: str | None
    assumption: str | None = None  # set when the user's word could have meant another country


@dataclass(frozen=True)
class Country:
    name: str
    iso_alpha3: str | None
    iso_alpha2: str | None


class CountryTable:
    def __init__(self, countries: Iterable[Country], aliases: dict[str, str] | None = None) -> None:
        self._by_name = {country.name: country for country in countries}
        self._by_folded_name = {name.casefold(): name for name in self._by_name}
        self._by_code = {
            code: country.name
            for country in self._by_name.values()
            for code in (country.iso_alpha2, country.iso_alpha3)
            if code is not None
        }
        self._aliases = ALIASES if aliases is None else aliases

    def __contains__(self, name: object) -> bool:
        return name in self._by_name

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._by_name)

    def iso_alpha3(self, name: str) -> str | None:
        """The alpha-3 code of a registry name; None for an unknown name and for a value without a code."""
        country = self._by_name.get(name.strip())
        return None if country is None else country.iso_alpha3

    def resolve(self, text: str) -> str | None:
        """The registry's spelling of the country `text` names, or None."""
        found = self.match(text)
        return None if found is None else found.name

    def match(self, text: str) -> CountryMatch | None:
        """The registry country a user's words name, with its code and any assumption made, or None.

        A code counts only when typed in capitals, so that the words "in", "it" and "no" are not read as
        India, Italy and Norway.
        """
        word = " ".join(text.split())
        folded = word.casefold()
        name = self._by_folded_name.get(folded) or self._aliases.get(folded)
        if name is None and word.isupper():
            name = self._by_code.get(word)
        if name is not None and name in self._by_name:
            return CountryMatch(name, self._by_name[name].iso_alpha3)
        if folded in ASSUMED_ALIASES and ASSUMED_ALIASES[folded][0] in self._by_name:
            name, assumption = ASSUMED_ALIASES[folded]
            return CountryMatch(name, self._by_name[name].iso_alpha3, assumption)
        return None

    def suggest(self, text: str, limit: int = 3) -> list[str]:
        """The registry names closest to `text`, to offer when it names no country."""
        close = difflib.get_close_matches(text.casefold(), list(self._by_folded_name), n=limit, cutoff=0.6)
        return [self._by_folded_name[name] for name in close]


@cache
def load_country_table() -> CountryTable:
    """The committed table, `data/countries.json`."""
    rows = json.loads(COUNTRIES_FILE.read_text(encoding="utf-8"))
    return CountryTable(Country(row["name"], row["iso_alpha3"], row["iso_alpha2"]) for row in rows)
