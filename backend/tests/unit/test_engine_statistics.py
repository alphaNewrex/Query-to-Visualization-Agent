"""Statistics of a number that belongs to a trial: what is counted per group, what is said about it."""

import pytest

from ctviz.contract.invariants import check_invariants
from ctviz.contract.response import BarChart, Citation, Metric
from ctviz.engine.evidence import projection
from ctviz.engine.lower import MeasureSpec, PointRows

from .engine_memory import Run, answer, dim, plan, public, record, rows_of

pytestmark = pytest.mark.anyio


def stat(statistic: str, of: str, *dimensions: str) -> dict[str, object]:
    return {
        "kind": "aggregate" if dimensions else "total",
        "dimension": dimensions[0] if dimensions else None,
        "series": None,
        "time_unit": None,
        "top_n": None,
        "statistic": statistic,
        "of": of,
    }


async def run(statistic: str, of: str, *keys: str, studies: list, **kwargs: object) -> Run:
    analysis = stat(statistic, of, *keys)
    if not keys:
        analysis = {"kind": "total", "statistic": statistic, "of": of}
    p = plan(
        *(dim(key, "axis" if n == 0 else "series") for n, key in enumerate(keys)),
        pub=public(analysis),
        measure=MeasureSpec(statistic, of),  # type: ignore[arg-type]
        relation="series" if len(keys) > 1 else None,
        **kwargs,  # type: ignore[arg-type]
    )
    return await answer(p, studies)


SITES = [
    record(1, locations=[("France", None)] * 2 + [("Germany", None)] * 8, enrollment=100),
    record(2, locations=[("France", None)], enrollment=40),
]


async def test_the_sites_of_a_trial_are_counted_in_each_country_it_has_them_in() -> None:
    result = await run("sum", "site_count", "country", studies=SITES)

    by_country = {
        row["country"]: (row["sum_site_count"], row["trial_count"]) for row in rows_of(result.response)
    }
    # Trial 1 has two sites in France and eight in Germany: not ten in each.
    assert by_country == {"France": (3, 2), "Germany": (8, 1)}
    assert "overlap" not in " ".join(w.code for w in result.response.meta.warnings)


async def test_a_median_of_sites_by_country_is_of_the_sites_in_that_country() -> None:
    result = await run("median", "site_count", "country", studies=SITES)

    by_country = {row["country"]: row["median_site_count"] for row in rows_of(result.response)}
    assert by_country == {"France": 1.5, "Germany": 8}
    assert any("Within a country or a state" in note for note in result.response.meta.assumptions)


async def test_sites_by_state_are_the_sites_in_that_state() -> None:
    trials = [
        record(1, locations=[("United States", "Ohio")] * 3 + [("United States", "Texas")]),
        record(2, locations=[("United States", "Ohio"), ("Canada", "Ohio")]),
    ]

    result = await run("sum", "site_count", "state", studies=trials)

    by_state = {row["state"]: row["sum_site_count"] for row in rows_of(result.response)}
    # With no country named every site counts, and a state name two countries share is one group.
    assert by_state == {"Ohio": 5, "Texas": 1}


async def test_a_two_dimension_cell_counts_the_sites_of_its_country_only() -> None:
    result = await run("sum", "site_count", "country", "phase", studies=SITES)

    cells = {(row["country"], row["phase"]): row["sum_site_count"] for row in rows_of(result.response)}
    assert cells[("France", "Phase 2")] == 3 and cells[("Germany", "Phase 2")] == 8


async def test_a_sum_of_enrollment_by_country_is_not_presented_as_a_total_of_each_country() -> None:
    result = await run("sum", "enrollment", "country", studies=SITES)

    response = result.response
    codes = [warning.code for warning in response.meta.warnings]
    assert "sum_over_overlapping_groups" in codes
    warning = next(w for w in response.meta.warnings if w.code == "sum_over_overlapping_groups")
    assert "counts in full in every country group" in warning.message
    assert "not the enrollment within each group" in warning.message
    assert "each trial counts in full in every group it is in" in response.message


@pytest.mark.parametrize(
    ("statistic", "of", "keys"),
    [
        ("median", "enrollment", ("country",)),  # a typical trial, whatever the groups
        ("sum", "enrollment", ("phase",)),  # exclusive groups: a split of the total
        ("sum", "site_count", ("country",)),  # the group's own sites
        ("sum", "site_count", ("state",)),
    ],
)
async def test_no_warning_where_the_number_is_the_groups_own(
    statistic: str, of: str, keys: tuple[str, ...]
) -> None:
    trials = [*SITES, record(3, locations=[("France", "Paris")], enrollment=5)]

    result = await run(statistic, of, *keys, studies=trials)

    assert "sum_over_overlapping_groups" not in [w.code for w in result.response.meta.warnings]
    assert "counts in full" not in result.response.message


async def test_a_sum_by_a_multi_valued_field_that_does_not_locate_sites_is_also_warned() -> None:
    result = await run("sum", "site_count", "drug", studies=SITES)

    assert "sum_over_overlapping_groups" in [w.code for w in result.response.meta.warnings]


# --- what a statistic is the statistic of ----------------------------------------------------------------


async def test_the_assumption_says_how_many_enrollment_counts_are_actual_planned_and_zero() -> None:
    here = [("France", None)]
    trials = [
        record(1, enrollment=100, enrollment_type="ACTUAL", locations=here),
        record(2, enrollment=0, enrollment_type="ACTUAL", locations=here),
        record(3, enrollment=50, enrollment_type="ESTIMATED", locations=here),
        record(4, enrollment=None, locations=here),
    ]

    result = await run("median", "enrollment", studies=trials)

    notes = result.response.meta.assumptions
    assert "Enrollment counts used: 2 actual, 1 estimated (planned); 1 of them are zero and kept." in notes
    assert any("a trial that reports zero (a withdrawn trial) is kept" in note for note in notes)


async def test_the_assumption_says_how_many_durations_end_on_a_planned_completion_date() -> None:
    trials = [
        record(1, start="2020-01", completion="2021-01", completion_type="ACTUAL"),
        record(2, start="2020-01", completion="2022-01", completion_type="ESTIMATED"),
        record(3, start="2020-01", completion="2023-01", completion_type="ESTIMATED"),
        record(4, start="2020-01", completion="2021-06"),  # a record that does not say
    ]

    result = await run("median", "duration_months", studies=trials)

    notes = result.response.meta.assumptions
    assert (
        "Durations used: start dates 4 not stated as either; completion dates 1 actual, "
        "2 estimated (planned), 1 not stated as either." in notes
    )
    assert any("whether each is actual or estimated" in note and "calendar months" in note for note in notes)


def test_a_duration_projects_the_types_of_its_dates() -> None:
    pub = public({"kind": "total", "statistic": "median", "of": "duration_months"})
    p = plan(pub=pub, measure=MeasureSpec("median", "duration_months"))

    fields = projection(p, p.scopes[0])

    assert {"StartDate", "StartDateType", "CompletionDate", "CompletionDateType"} <= set(fields)


# --- what a site count cites -----------------------------------------------------------------------------


def paths(citations: list[Citation]) -> list[tuple[str, str | None]]:
    return [(c.field.rsplit("contactsLocationsModule.", 1)[-1], c.excerpt) for c in citations]


async def test_a_site_count_cites_where_the_list_of_sites_ends() -> None:
    trials = [record(1, locations=[("France", None), ("Germany", None), ("Spain", None)])]

    result = await run("median", "site_count", studies=trials)

    viz = result.response.visualization
    assert isinstance(viz, Metric)
    # Three sites: the last lies at index 2 and index 3 holds nothing, so the list has exactly three.
    assert paths(viz.data[0].citations) == [("locations[2].country", "Spain"), ("locations[3]", None)]
    assert check_invariants(result.response, result.memory) == []


async def test_the_sites_in_a_country_also_cite_the_first_and_last_of_them() -> None:
    trials = [record(1, locations=[("France", None), ("Germany", None), ("France", None), ("Spain", None)])]

    result = await run("sum", "site_count", "country", studies=trials)

    viz = result.response.visualization
    assert isinstance(viz, BarChart)
    france = next(row for row in viz.data if row.model_extra and row.model_extra["country"] == "France")
    assert france.model_extra and france.model_extra["sum_site_count"] == 2
    assert paths(france.citations) == [
        ("locations[0].country", "France"),
        ("locations[2].country", "France"),
        ("locations[3].country", "Spain"),
        ("locations[4]", None),
    ]
    assert check_invariants(result.response, result.memory) == []


async def test_a_site_with_no_country_is_still_a_site() -> None:
    trials = [
        record(1, locations=[(None, None)]),  # the registry lists one site and it names no country
        record(2, locations=[("France", None), (None, None)]),
        record(3, locations=[]),
    ]

    total = await run("sum", "site_count", studies=trials)
    scatter = await answer(
        plan(
            pub=public({"kind": "relate", "x": "enrollment", "y": "site_count", "color_by": None}),
            rows=PointRows("enrollment", "site_count", None),
        ),
        [record(1, enrollment=5, locations=[(None, None)]), record(2, enrollment=5, locations=[])],
    )

    viz = total.response.visualization
    assert isinstance(viz, Metric)
    # Three sites over two trials; trial 3 lists none, and a scatter plot leaves it out as a statistic does.
    assert viz.data[0].model_extra["sum_site_count"] == 3 and viz.data[0].citation_count == 2
    assert [row["site_count"] for row in rows_of(scatter.response)] == [1]
    counts = scatter.response.meta.counts
    assert counts is not None and [e.reason for e in counts.series[0].trials_excluded] == ["no_site_count"]
    assert check_invariants(total.response, total.memory) == []
