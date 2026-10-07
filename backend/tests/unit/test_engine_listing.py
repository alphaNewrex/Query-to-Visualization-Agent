"""Trial lists and scatter plots: how many of how many, and which compared group each row belongs to."""

import pytest

from ctviz.contract.invariants import check_invariants
from ctviz.contract.response import ScatterPlot, Table
from ctviz.ctgov import essie
from ctviz.ctgov.params import BoundTerm, Scope
from ctviz.engine.lower import ListRows, PointRows

from .engine_memory import answer, plan, public, record, rows_of, scope

pytestmark = pytest.mark.anyio

TRIALS = [
    record(
        n,
        sponsor="Acme" if n % 2 else "Bolt",
        enrollment=n * 10,
        countries=("France",) if n <= 20 else ("France", "Spain"),
        start="2020-01",
        completion="2021-01",
    )
    for n in range(1, 31)
]
LIST = public({"kind": "trial_list", "sort_by": "enrollment", "order": "desc", "limit": 5})
SCATTER = public({"kind": "relate", "x": "enrollment", "y": "duration_months", "color_by": None})


def country(name: str, scope_id: str) -> Scope:
    term = BoundTerm(
        kind="country",
        text=name,
        term=name,
        parameter=None,
        expr=essie.area("LocationCountry", name),
        definition="country_exact",
        note="",
    )
    return scope(name, scope_id, terms=(term,))


async def test_a_list_cut_short_says_how_many_of_the_matching_trials_it_shows() -> None:
    p = plan(pub=LIST, rows=ListRows("enrollment", "desc"), top_n=5)

    run = await answer(p, TRIALS)

    assert run.response.message == "5 of 30 trials, ordered by largest enrollment."
    assert run.response.visualization.subtitle.startswith("5 of 30 trials · ")
    assert len(rows_of(run.response)) == 5


async def test_a_list_that_shows_everything_that_matches_says_so_once() -> None:
    p = plan(pub=LIST, rows=ListRows("enrollment", "desc"), top_n=50)

    run = await answer(p, TRIALS)

    assert run.response.message == "30 trials, ordered by largest enrollment."


async def test_compared_lists_carry_a_group_column_and_count_each_group() -> None:
    scopes = (country("France", "s0"), country("Spain", "s1"))
    p = plan(pub=LIST, rows=ListRows("enrollment", "desc"), top_n=3, scopes=scopes, compare_kind="country")

    run = await answer(p, TRIALS)

    response = run.response
    viz = response.visualization
    assert isinstance(viz, Table)
    assert "group" in [column.field for column in viz.encoding.columns]
    assert [column.title for column in viz.encoding.columns if column.field == "group"] == ["Country"]
    rows = rows_of(response)
    assert [(row["group"], row["nct_id"]) for row in rows] == [
        ("France", "NCT00000030"),
        ("France", "NCT00000029"),
        ("France", "NCT00000028"),
        ("Spain", "NCT00000030"),
        ("Spain", "NCT00000029"),
        ("Spain", "NCT00000028"),
    ]
    assert response.message == "France: 3 of 30, Spain: 3 of 10 trials, ordered by largest enrollment."
    assert "of 3 trials" not in response.message
    assert check_invariants(response, run.memory) == []


async def test_compared_scatter_points_name_their_group() -> None:
    scopes = (country("France", "s0"), country("Spain", "s1"))
    p = plan(
        pub=SCATTER,
        rows=PointRows("enrollment", "duration_months", None),
        scopes=scopes,
        compare_kind="country",
    )

    run = await answer(p, TRIALS[18:24])

    viz = run.response.visualization
    assert isinstance(viz, ScatterPlot)
    assert viz.encoding.series is not None
    assert (viz.encoding.series.field, viz.encoding.series.title) == ("group", "Country")
    assert viz.encoding.series.domain == ["France", "Spain"]
    groups = [row["group"] for row in rows_of(run.response)]
    assert groups == ["France"] * 6 + ["Spain"] * 4
    assert check_invariants(run.response, run.memory) == []


async def test_a_table_row_quotes_every_cell_it_shows() -> None:
    p = plan(pub=LIST, rows=ListRows("enrollment", "desc"), top_n=1)

    run = await answer(p, TRIALS)

    viz = run.response.visualization
    assert isinstance(viz, Table)
    cited = [(c.field.removeprefix("protocolSection."), c.excerpt) for c in viz.data[0].citations]
    assert cited == [
        ("identificationModule.nctId", "NCT00000030"),
        ("identificationModule.briefTitle", "Trial 30"),
        ("designModule.enrollmentInfo.count", "300"),
        ("designModule.phases[0]", "PHASE2"),
        ("statusModule.overallStatus", "COMPLETED"),
        ("statusModule.startDateStruct.date", "2020-01"),
        ("sponsorCollaboratorsModule.leadSponsor.name", "Bolt"),
    ]
    assert check_invariants(run.response, run.memory) == []


async def test_a_table_row_quotes_an_absent_cell_as_absent() -> None:
    trials = [record(1, enrollment=None, start=None, phases=())]
    p = plan(pub=LIST, rows=ListRows("enrollment", "desc"), top_n=1)

    run = await answer(p, trials)

    viz = run.response.visualization
    assert isinstance(viz, Table)
    absent = {c.field.removeprefix("protocolSection.") for c in viz.data[0].citations if c.excerpt is None}
    assert {
        "designModule.enrollmentInfo.count",
        "statusModule.startDateStruct.date",
        "designModule.phases",
    } <= absent
    assert check_invariants(run.response, run.memory) == []


async def test_the_headline_of_an_ascending_list_says_smallest_like_its_title() -> None:
    asc = public({"kind": "trial_list", "sort_by": "enrollment", "order": "asc", "limit": 5})
    dated = public({"kind": "trial_list", "sort_by": "start_date", "order": "asc", "limit": 5})

    smallest = await answer(plan(pub=asc, rows=ListRows("enrollment", "asc"), top_n=5), TRIALS)
    earliest = await answer(plan(pub=dated, rows=ListRows("start_date", "asc"), top_n=5), TRIALS)

    assert smallest.response.visualization.title == "Trials with the smallest enrollment"
    assert smallest.response.message == "5 of 30 trials, ordered by smallest enrollment."
    assert earliest.response.visualization.title == "Trials by start date, earliest first"
    assert earliest.response.message == "5 of 30 trials, ordered by start date, earliest first."


async def test_a_scatter_plot_says_why_a_trial_was_left_out_in_words() -> None:
    p = plan(pub=SCATTER, rows=PointRows("enrollment", "duration_months", None))
    trials = [record(1, enrollment=5, start="2020-01", completion=None, locations=[("France", None)])]

    # With nothing to plot the answer is `no_data`; the exclusion is in the counts of a mixed set.
    mixed = [*trials, record(2, enrollment=5, start="2020-01", completion="2021-01")]
    run = await answer(p, mixed)

    counts = run.response.meta.counts
    assert counts is not None
    (excluded,) = counts.series[0].trials_excluded
    assert excluded.reason == "no_duration_months"
    assert excluded.message == (
        "No start and completion date to measure a duration from, or a completion before the start"
    )
