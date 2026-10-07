"""Essie: user words cannot become an expression, and enum tokens that read as operators are quoted.

The rows are the cases measured against the live registry (section 4.6 of the plan): the text on the
left is what a user could type, and the registry would have run it as an expression.
"""

import pytest

from ctviz.ctgov import essie
from ctviz.ctgov.essie import Expr

# --- literal: words only ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("typed", "searched"),
    [
        # The operator `ALL` is the whole registry (606,007 studies); escaped, it is the term (4,572).
        ("ALL", r"\ALL"),
        ("ALL,", r"\ALL,"),
        ("AML,ALL", r"AML,\ALL"),
        # A backslash typed by the user is dropped like a bracket: only `literal` adds escapes.
        ("AML,\\ALL", r"AML, \ALL"),
        ("NOT", r"\NOT"),
        ("AND", r"\AND"),
        ("MISSING", r"\MISSING"),
        ("AREA", r"\AREA"),
        ("heart attack OR stroke", r"heart attack \OR stroke"),
        # Lower case and longer words are terms already (measured: `all` and `ALLERGY` need no escape).
        ("all", "all"),
        ("or", "or"),
        ("ALLERGY", "ALLERGY"),
        ("NOT-ALL", r"\NOT-\ALL"),
        # An injected filter loses its brackets, so it is words, and its operator word is escaped.
        ("AREA[Phase]PHASE4", r"\AREA Phase PHASE4"),
        ('x[1](2)"3"\\4', "x 1 2 3 4"),
        # Real names pass unchanged (measured counts in the plan: 4,262, 344, 2,184 and 7).
        ("Alzheimer's disease", "Alzheimer's disease"),
        ("Merck KGaA, Darmstadt, Germany", "Merck KGaA, Darmstadt, Germany"),
        ("Merck Sharp & Dohme LLC", "Merck Sharp & Dohme LLC"),
        ("NS-065/NCNP-01", "NS-065/NCNP-01"),
    ],
)
def test_literal_leaves_words_alone_and_escapes_every_operator(typed: str, searched: str) -> None:
    assert essie.literal(typed) == searched


@pytest.mark.parametrize(
    ("typed", "searched"),
    [
        ("  heart \t attack\n", "heart attack"),
        ("heart\x00 attack\u200b", "heart attack"),
        (
            "\uff21\uff2c\uff2c",
            r"\ALL",
        ),  # full-width letters are folded first, so they cannot hide an operator
        ("Türkiye", "Türkiye"),
    ],
)
def test_literal_normalises_before_it_looks_for_operators(typed: str, searched: str) -> None:
    assert essie.literal(typed) == searched


@pytest.mark.parametrize("typed", ["", "   ", "[]()", '""', "\\", "-", "\x00\u200b"])
def test_literal_rejects_text_with_nothing_to_search_for(typed: str) -> None:
    with pytest.raises(ValueError, match="no letter or digit"):
        essie.literal(typed)


# --- area: enum tokens and phrases -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("piece", "value", "expected"),
    [
        ("Phase", "PHASE2", "AREA[Phase]PHASE2"),
        # Measured: NA, NONE and OTHER work unquoted.
        ("Phase", "NA", "AREA[Phase]NA"),
        ("DesignMasking", "NONE", "AREA[DesignMasking]NONE"),
        ("LeadSponsorClass", "OTHER", "AREA[LeadSponsorClass]OTHER"),
        ("HasResults", "true", "AREA[HasResults]true"),
        # `AREA[Sex]ALL` is the operator and matches 606,007; quoted it is the enum value, 521,619.
        ("Sex", "ALL", 'AREA[Sex]"ALL"'),
        ("Sex", "FEMALE", "AREA[Sex]FEMALE"),
        # Free text is always a phrase.
        ("LocationCountry", "United States", 'AREA[LocationCountry]"United States"'),
        ("LocationCountry", "Turkey (Türkiye)", 'AREA[LocationCountry]"Turkey (Türkiye)"'),
        ("LocationCountry", "France", 'AREA[LocationCountry]"France"'),
        ("InterventionName", "pembrolizumab", 'AREA[InterventionName]"pembrolizumab"'),
    ],
)
def test_area_quotes_free_text_and_operator_lookalikes(piece: str, value: str, expected: str) -> None:
    assert essie.area(piece, value) == expected


def test_area_cannot_be_closed_early_by_its_value() -> None:
    expr = essie.area("InterventionName", 'x" OR AREA[Phase]PHASE4 OR "y')

    assert expr == 'AREA[InterventionName]"x OR AREA[Phase]PHASE4 OR y"'
    assert expr.count('"') == 2


def test_area_quotes_the_escaped_form_of_an_operator_word_as_the_word_itself() -> None:
    assert essie.area("InterventionName", essie.literal("ALL")) == 'AREA[InterventionName]"ALL"'


@pytest.mark.parametrize("piece", ["", "Phase]", "Phase]PHASE4 OR AREA[Phase", "1Phase", "A B"])
def test_a_piece_name_is_never_text(piece: str) -> None:
    with pytest.raises(ValueError, match="Not an area name"):
        essie.area(piece, "PHASE2")


def test_area_rejects_an_empty_phrase() -> None:
    with pytest.raises(ValueError, match="empty"):
        essie.area("LocationCountry", ' " ')


# --- the other builders ------------------------------------------------------------------------------


def test_any_of_groups_enum_tokens() -> None:
    # Measured: AREA[Phase](PHASE2 OR PHASE3) is 132,833 = 90,470 + 49,980 - 7,617.
    assert essie.any_of("Phase", ["PHASE2", "PHASE3"]) == "AREA[Phase](PHASE2 OR PHASE3)"
    assert essie.any_of("Phase", ["PHASE2"]) == "AREA[Phase]PHASE2"
    assert essie.any_of("Sex", ["ALL", "FEMALE"]) == 'AREA[Sex]("ALL" OR FEMALE)'


@pytest.mark.parametrize("value", ["phase2", "Phase 2", "PHASE2)", "PHASE2 OR PHASE3", ""])
def test_any_of_takes_enum_tokens_and_never_text(value: str) -> None:
    with pytest.raises(ValueError, match="Not an enum token"):
        essie.any_of("Phase", ["PHASE1", value])


def test_any_of_needs_a_value() -> None:
    with pytest.raises(ValueError, match="At least one"):
        essie.any_of("Phase", [])


def test_all_of_writes_the_exclusive_phase_buckets() -> None:
    assert essie.all_of("Phase", ["PHASE1", "PHASE2"]) == "AREA[Phase](PHASE1 AND PHASE2)"
    assert essie.all_of("Phase", ["PHASE1"], but_not=["PHASE2"]) == "AREA[Phase](PHASE1 AND NOT PHASE2)"
    assert (
        essie.all_of("Phase", ["PHASE2"], but_not=["PHASE1", "PHASE3"])
        == "AREA[Phase](PHASE2 AND NOT PHASE1 AND NOT PHASE3)"
    )
    assert essie.all_of("Phase", ["PHASE4"]) == "AREA[Phase]PHASE4"


@pytest.mark.parametrize(
    ("lo", "hi", "expected"),
    [
        ("2015-01-01", None, "AREA[StartDate]RANGE[2015-01-01,MAX]"),
        ("2015-01-01", "2015-12-31", "AREA[StartDate]RANGE[2015-01-01,2015-12-31]"),
        (None, "2014-12-31", "AREA[StartDate]RANGE[MIN,2014-12-31]"),
        (100, 249, "AREA[StartDate]RANGE[100,249]"),
        (10000, None, "AREA[StartDate]RANGE[10000,MAX]"),
        (0, 0, "AREA[StartDate]RANGE[0,0]"),
    ],
)
def test_range_has_inclusive_ends_and_an_open_end_for_none(
    lo: str | int | None, hi: str | int | None, expected: str
) -> None:
    assert essie.range_("StartDate", lo, hi) == expected


@pytest.mark.parametrize("end", ["2015", "2015-01", "MAX", "2015-01-01]", -1, True, 1.5])
def test_range_ends_are_dates_or_whole_numbers(end: object) -> None:
    with pytest.raises(ValueError, match="Not a date"):
        essie.range_("StartDate", end, None)  # type: ignore[arg-type]


def test_a_range_needs_an_end() -> None:
    with pytest.raises(ValueError, match="at least one end"):
        essie.range_("StartDate", None, None)


def test_missing() -> None:
    assert essie.missing("Phase") == "AREA[Phase]MISSING"
    assert essie.missing("Intervention:size") == "AREA[Intervention:size]MISSING"


def test_and_parenthesises_every_part() -> None:
    first, second = essie.any_of("Phase", ["PHASE2", "PHASE3"]), essie.range_("StartDate", "2015-01-01", None)

    assert (
        essie.and_(first, second)
        == "(AREA[Phase](PHASE2 OR PHASE3)) AND (AREA[StartDate]RANGE[2015-01-01,MAX])"
    )
    assert essie.and_(first) == first
    with pytest.raises(ValueError, match="at least one"):
        essie.and_()


def test_not_keeps_its_operand_whole_inside_a_conjunction() -> None:
    both = essie.and_(essie.area("Phase", "PHASE1"), essie.area("Sex", "FEMALE"))
    negated = essie.not_(both)

    assert negated == "NOT ((AREA[Phase]PHASE1) AND (AREA[Sex]FEMALE))"
    assert essie.and_(Expr("AREA[Phase]PHASE2"), negated) == (
        "(AREA[Phase]PHASE2) AND (NOT ((AREA[Phase]PHASE1) AND (AREA[Sex]FEMALE)))"
    )


def test_a_search_phrase_is_quoted_in_its_area() -> None:
    assert (
        essie.search_phrase("ConditionSearch", "type 2 diabetes") == 'AREA[ConditionSearch]"type 2 diabetes"'
    )
    assert essie.search_phrase("ConditionSearch", 'a "b" NOT c') == 'AREA[ConditionSearch]"a b NOT c"'
