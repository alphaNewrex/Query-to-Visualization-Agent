"""The country table: the committed JSON and the exact lookup."""

import pycountry
import pytest

from ctviz.catalog.countries import CountryMatch, load_country_table

TABLE = load_country_table()


def test_the_table_holds_the_226_registry_names_trimmed() -> None:
    assert len(TABLE.names) == 226
    assert "Bonaire, Saint Eustatius and Saba" in TABLE
    assert all(name == name.strip() for name in TABLE.names)


def test_every_code_is_a_real_iso_code_except_kosovos() -> None:
    codes = {name: TABLE.iso_alpha3(name) for name in TABLE.names}
    assert codes["Kosovo"] == "XKX"
    assert [name for name, code in codes.items() if code is None] == [
        "Federal Republic of Yugoslavia",
        "Netherlands Antilles",
        "Serbia and Montenegro",
        "Virgin Islands",
    ]
    assert all(pycountry.countries.get(alpha_3=code) for code in codes.values() if code not in {None, "XKX"})


def test_the_overrides_for_names_pycountry_spells_differently() -> None:
    assert TABLE.iso_alpha3("Turkey (Türkiye)") == "TUR"
    assert TABLE.iso_alpha3("Russia") == "RUS"
    assert TABLE.iso_alpha3("Democratic Republic of the Congo") == "COD"
    assert TABLE.iso_alpha3("Bonaire, Saint Eustatius and Saba ") == "BES"
    assert TABLE.iso_alpha3("Atlantis") is None


@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("United States", "United States"),
        ("united states", "United States"),
        ("USA", "United States"),
        ("us", "United States"),
        ("United States of America", "United States"),
        ("UK", "United Kingdom"),
        ("Britain", "United Kingdom"),
        ("Türkiye", "Turkey (Türkiye)"),
        ("Ivory Coast", "C\u00f4te d\u2019Ivoire"),
        ("DE", "Germany"),
        ("DEU", "Germany"),
        ("  south   korea ", "South Korea"),
        ("Curacao", "Curacao"),
    ],
)
def test_resolve_finds_names_aliases_and_codes(text: str, name: str) -> None:
    assert TABLE.resolve(text) == name
    match = TABLE.match(text)
    assert match is not None
    assert match.name == name
    assert match.assumption is None


def test_a_match_carries_the_iso_code() -> None:
    assert TABLE.match("Germany") == CountryMatch("Germany", "DEU")
    assert TABLE.match("Virgin Islands") == CountryMatch("Virgin Islands", None)


def test_korea_is_read_as_south_korea_with_an_assumption() -> None:
    assert TABLE.resolve("Korea") == "South Korea"
    match = TABLE.match("Korea")
    assert match is not None
    assert (match.name, match.iso_alpha3) == ("South Korea", "KOR")
    assert match.assumption is not None
    assert "North Korea" in match.assumption


@pytest.mark.parametrize("text", ["in", "it", "no", "Germny", "Atlantis", ""])
def test_resolve_does_not_guess(text: str) -> None:
    """Lower-case words are not country codes, and nothing is fuzzy."""
    assert TABLE.resolve(text) is None
    assert TABLE.match(text) is None


def test_kosovo_is_not_matched_to_serbia() -> None:
    assert TABLE.resolve("Kosovo") == "Kosovo"


def test_suggestions_are_close_registry_names() -> None:
    assert TABLE.suggest("Germny") == ["Germany"]
    assert TABLE.suggest("qqqqqq") == []
