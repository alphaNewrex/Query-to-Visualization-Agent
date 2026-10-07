"""Scopes written as request parameters, and the canonical URL: one scope, one URL, whatever the order."""

import pytest

from ctviz.ctgov import essie
from ctviz.ctgov.essie import Expr
from ctviz.ctgov.params import BoundTerm, DateRange, Params, Scope, canonical_url

BASE = "https://clinicaltrials.gov/api/v2"


def search_term(text: str, parameter: str = "query.intr") -> BoundTerm:
    return BoundTerm(
        kind="drug",
        text=text,
        term=essie.literal(text),
        parameter=parameter,
        expr=None,
        definition="intervention_search",
        note="",
    )


def country_term(name: str) -> BoundTerm:
    return BoundTerm(
        kind="country",
        text=name,
        term=name,
        parameter=None,
        expr=essie.area("LocationCountry", name),
        definition="country_exact",
        note="",
    )


def scope(
    *terms: BoundTerm,
    enum_filters: dict[str, tuple[str, ...]] | None = None,
    date_range: DateRange | None = None,
    extra: tuple[Expr, ...] = (),
) -> Scope:
    return Scope(
        id="s0",
        label=None,
        terms=terms,
        enum_filters=enum_filters or {},
        date_range=date_range,
        extra=extra,
    )


# --- Scope.params -----------------------------------------------------------------------------------


def test_a_scope_with_one_drug_is_one_search_parameter() -> None:
    params = scope(search_term("pembrolizumab")).params()

    assert params.pairs() == (("query.intr", "pembrolizumab"),)


def test_terms_of_one_parameter_must_all_match() -> None:
    params = scope(search_term("pembrolizumab"), search_term("nivolumab")).params()

    assert params.pairs() == (("query.intr", "(pembrolizumab) AND (nivolumab)"),)


def test_terms_keep_their_user_text_as_words_not_operators() -> None:
    params = scope(search_term("ALL", "query.cond")).params()

    assert params.pairs() == (("query.cond", r"\ALL"),)


def test_every_filter_ends_up_in_one_canonical_scope_expression() -> None:
    chosen = scope(
        search_term("pembrolizumab"),
        search_term("lung cancer", "query.cond"),
        country_term("United States"),
        enum_filters={
            "phase": ("PHASE2", "PHASE3"),
            "overall_status": ("RECRUITING", "ACTIVE_NOT_RECRUITING"),
            "sponsor_class": ("INDUSTRY",),
        },
        date_range=DateRange("StartDate", "2015-01-01", None),
        extra=(essie.not_(essie.missing("LocationCountry")),),
    )

    assert chosen.params().pairs() == (
        ("query.cond", "lung cancer"),
        ("query.intr", "pembrolizumab"),
        ("filter.overallStatus", "RECRUITING|ACTIVE_NOT_RECRUITING"),
        (
            "filter.advanced",
            '(AREA[LocationCountry]"United States")'
            " AND (AREA[Phase](PHASE2 OR PHASE3))"
            " AND (AREA[LeadSponsorClass]INDUSTRY)"
            " AND (AREA[StartDate]RANGE[2015-01-01,MAX])"
            " AND (NOT (AREA[LocationCountry]MISSING))",
        ),
    )


def test_the_same_scope_gives_the_same_parameters_whatever_the_insertion_order() -> None:
    filters = {"phase": ("PHASE1",), "study_type": ("INTERVENTIONAL",), "overall_status": ("COMPLETED",)}
    reordered = dict(reversed(filters.items()))

    first = scope(search_term("x"), enum_filters=filters).params()
    second = scope(search_term("x"), enum_filters=reordered).params()

    assert first == second
    assert canonical_url(BASE, first) == canonical_url(BASE, second)


def test_a_scope_with_nothing_in_it_has_no_parameters() -> None:
    assert scope().params() == Params()
    assert scope(enum_filters={"phase": ()}).params() == Params()


def test_a_filter_without_a_piece_of_its_own_is_refused() -> None:
    with pytest.raises(ValueError, match="No filter is defined for 'enrollment'"):
        scope(enum_filters={"enrollment": ("5",)}).params()


@pytest.mark.parametrize(
    "term",
    [
        {"parameter": None, "expr": None},
        {"parameter": "query.intr", "expr": Expr("AREA[Phase]PHASE2")},
        {"parameter": "filter.advanced", "expr": None},
        {"parameter": "query.intr&pageSize=1000", "expr": None},
    ],
)
def test_a_term_has_exactly_one_way_into_the_request(term: dict[str, object]) -> None:
    with pytest.raises(ValueError, match=r"either to a query parameter|Not a search parameter"):
        BoundTerm(kind="drug", text="x", term="x", definition="term_search", note="", **term)  # type: ignore[arg-type]


def test_a_date_range_has_inclusive_days_and_open_ends() -> None:
    assert DateRange("StartDate", "2015-01-01", "2015-12-31").expr() == (
        "AREA[StartDate]RANGE[2015-01-01,2015-12-31]"
    )
    assert DateRange("StartDate", None, "2014-12-31").expr() == "AREA[StartDate]RANGE[MIN,2014-12-31]"


# --- Params.narrowed_by ----------------------------------------------------------------------------


def test_narrowing_adds_to_the_one_advanced_filter() -> None:
    year = essie.range_("StartDate", "2015-01-01", "2015-12-31")
    phase = essie.area("Phase", "PHASE2")

    bare = Params((("query.intr", "pembrolizumab"),))
    narrowed = bare.narrowed_by(phase)

    assert bare.advanced is None
    assert narrowed.pairs() == (("query.intr", "pembrolizumab"), ("filter.advanced", "AREA[Phase]PHASE2"))
    assert narrowed.narrowed_by(year).advanced == (
        "(AREA[Phase]PHASE2) AND (AREA[StartDate]RANGE[2015-01-01,2015-12-31])"
    )


# --- canonical_url -----------------------------------------------------------------------------------


def test_the_count_form_of_a_search() -> None:
    url = canonical_url(
        BASE, scope(search_term("pembrolizumab")).params(), count_total=True, page_size=1, fields=["NCTId"]
    )

    assert url == f"{BASE}/studies?query.intr=pembrolizumab&countTotal=true&pageSize=1&fields=NCTId"


def test_a_bucket_sample_is_a_search_narrowed_by_the_bucket_expression() -> None:
    bucket = essie.range_("StartDate", "2015-01-01", "2015-12-31")
    params = scope(search_term("pembrolizumab")).params().narrowed_by(bucket)

    url = canonical_url(
        BASE,
        params,
        count_total=True,
        page_size=3,
        fields=["BriefTitle", "StartDate", "NCTId"],
        sort="@relevance",
    )

    assert url == (
        f"{BASE}/studies?query.intr=pembrolizumab"
        "&filter.advanced=AREA%5BStartDate%5DRANGE%5B2015-01-01,2015-12-31%5D"
        "&countTotal=true&pageSize=3&fields=NCTId,BriefTitle,StartDate&sort=%40relevance"
    )


def test_a_page_token_is_the_last_parameter() -> None:
    url = canonical_url(BASE, Params(), page_size=1000, fields=["Phase"], page_token="abc_DEF-123")

    assert url == f"{BASE}/studies?pageSize=1000&fields=NCTId,Phase&pageToken=abc_DEF-123"


def test_fields_have_one_canonical_order_and_no_repeats() -> None:
    a = canonical_url(BASE, Params(), fields=["StartDate", "Phase", "NCTId", "Phase"])
    b = canonical_url(BASE, Params(), fields=["NCTId", "Phase", "StartDate"])

    assert a == b == f"{BASE}/studies?fields=NCTId,Phase,StartDate"


def test_no_fields_means_whole_records() -> None:
    assert "fields" not in canonical_url(BASE, Params(), page_size=1)


def test_a_base_url_may_end_in_a_slash() -> None:
    assert canonical_url(f"{BASE}/", Params()) == canonical_url(BASE, Params()) == f"{BASE}/studies?"


@pytest.mark.parametrize(
    ("value", "encoded"),
    [
        ("heart attack", "heart%20attack"),
        ("Pembrolizumab+Lenvatinib", "Pembrolizumab%2BLenvatinib"),
        ("Merck Sharp & Dohme", "Merck%20Sharp%20%26%20Dohme"),
        ("a|b", "a%7Cb"),
        ("Türkiye", "T%C3%BCrkiye"),
        ("#fragment", "%23fragment"),
    ],
)
def test_values_are_percent_encoded(value: str, encoded: str) -> None:
    assert canonical_url(BASE, Params((("query.term", value),))) == f"{BASE}/studies?query.term={encoded}"


def test_a_value_cannot_add_a_parameter() -> None:
    url = canonical_url(BASE, Params((("query.term", "x&pageSize=1000&fields=Everything"),)), page_size=1)

    assert url.count("&") == 1
    assert url == f"{BASE}/studies?query.term=x%26pageSize%3D1000%26fields%3DEverything&pageSize=1"
