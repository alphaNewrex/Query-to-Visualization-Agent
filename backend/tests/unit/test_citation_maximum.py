"""`citations_per_datum` reaches 100 on both data paths, and costs the count path no extra request."""

import dataclasses
from typing import Any

import pytest
from pydantic import ValidationError

from ctviz.catalog.fields import Window
from ctviz.contract.invariants import check_invariants
from ctviz.contract.request import RequestOptions
from ctviz.engine.lower import ListRows, MeasureSpec, PointRows

from .engine_memory import Run, answer, dim, plan, public, record

pytestmark = pytest.mark.anyio

PHASES = public({"kind": "total", "statistic": None, "of": None})
YEARS = Window("year", "2020", "2022")
# 130 trials in phase 2, 3 in phase 1, none in phase 3.
TRIALS = [
    record(n, phases=("PHASE2",), start="2021-03", enrollment=n, completion="2022-03") for n in range(1, 131)
]
TRIALS += [record(n, phases=("PHASE1",), start="2020-06") for n in range(200, 203)]


def with_cap(p: Any, cap: int) -> Any:
    return dataclasses.replace(p, citations_per_datum=cap)


def cited(response: Any, **fields: object) -> Any:
    items = response.visualization.data
    return next(row for row in items if all((row.model_extra or {})[k] == v for k, v in fields.items()))


def trials_of(row: Any) -> set[str]:
    return {citation.nct_id for citation in row.citations}


def sound(run: Run) -> None:
    assert check_invariants(run.response, run.memory) == []


def test_the_request_accepts_up_to_one_hundred_and_defaults_to_five() -> None:
    assert RequestOptions().citations_per_datum == 5
    assert RequestOptions(citations_per_datum=100).citations_per_datum == 100
    with pytest.raises(ValidationError):
        RequestOptions(citations_per_datum=101)


@pytest.mark.parametrize("prefer_walk", [False, True], ids=["count", "walk"])
async def test_a_bar_with_more_than_a_hundred_trials_cites_a_hundred(prefer_walk: bool) -> None:
    p = plan(dim("phase"), pub=PHASES)

    run = await answer(with_cap(p, 100), TRIALS, one_page_max=10, prefer_walk=prefer_walk)

    big, small = cited(run.response, phase="Phase 2"), cited(run.response, phase="Phase 1")
    assert (len(trials_of(big)), big.citation_count) == (100, 130)
    assert (len(trials_of(small)), small.citation_count) == (3, 3)
    assert run.response.meta.citations.max_per_datum == 100
    assert len(run.response.references) == run.response.meta.citations.trials_cited == 103
    sound(run)


async def test_the_count_path_asks_for_the_sample_in_the_same_number_of_requests() -> None:
    p = plan(dim("phase"), pub=PHASES)

    five = await answer(with_cap(p, 5), TRIALS, one_page_max=10)
    hundred = await answer(with_cap(p, 100), TRIALS, one_page_max=10)

    assert five.execution.runs[0].strategy == hundred.execution.runs[0].strategy == "count_fan_out"
    assert len(hundred.memory.requests) == len(five.memory.requests)
    assert all("pageSize=5&" in r.url for r in five.memory.requests if r.origin == "execution")
    sample_calls = [r for r in hundred.memory.requests if "pageSize=100" in r.url]
    assert sample_calls and all(r.origin == "execution" for r in sample_calls)


@pytest.mark.parametrize("prefer_walk", [False, True], ids=["count", "walk"])
async def test_a_time_series_with_an_empty_period_cites_a_hundred_where_there_are_that_many(
    prefer_walk: bool,
) -> None:
    p = plan(dim("start_date", unit="year"), pub=PHASES, window=YEARS)

    run = await answer(with_cap(p, 100), TRIALS, one_page_max=10, prefer_walk=prefer_walk)

    busy, quiet, empty = (cited(run.response, start_year=year) for year in ("2021", "2020", "2022"))
    assert (len(trials_of(busy)), busy.citation_count) == (100, 130)
    assert (len(trials_of(quiet)), quiet.citation_count) == (3, 3)
    assert (trials_of(empty), empty.citation_count) == (set(), 0)
    sound(run)


async def test_a_network_node_and_a_link_cite_a_hundred() -> None:
    together = [record(n, countries=("France", "Spain")) for n in range(1, 121)]
    together += [
        record(n, countries=pair)
        for n, pair in enumerate([("Italy", "Peru")] * 3 + [("Chad", "Togo")] * 3, 200)
    ]
    p = plan(
        dim("country", "node"),
        dim("country", "node"),
        pub=public({"kind": "network", "source": "country", "target": "country", "link": None}),
        relation="network",
    )

    run = await answer(with_cap(p, 100), together)

    graph = run.response.visualization.data
    (link,) = [edge for edge in graph.edges if edge.trial_count == 120]
    assert (len(trials_of(link)), link.citation_count) == (100, 120)
    for node in [node for node in graph.nodes if node.id in ("France", "Spain")]:
        assert (len(trials_of(node)), node.citation_count) == (100, 120)
    sound(run)


async def test_a_statistic_cites_a_hundred_of_the_trials_it_rests_on() -> None:
    p = plan(
        dim("phase"),
        pub=public(
            {
                "kind": "aggregate",
                "dimension": "phase",
                "series": None,
                "time_unit": None,
                "top_n": 15,
                "statistic": "median",
                "of": "enrollment",
            }
        ),
        measure=MeasureSpec("median", "enrollment"),
    )

    run = await answer(with_cap(p, 100), TRIALS)

    assert run.execution.runs[0].strategy == "walk"
    big = cited(run.response, phase="Phase 2")
    assert (len(trials_of(big)), big.citation_count) == (100, 130)
    sound(run)


async def test_a_table_row_and_a_scatter_point_cite_their_own_trial() -> None:
    listed = await answer(
        with_cap(
            plan(
                pub=public({"kind": "trial_list", "sort_by": "enrollment", "order": "desc", "limit": 5}),
                rows=ListRows("enrollment", "desc"),
                top_n=5,
            ),
            100,
        ),
        TRIALS,
    )
    points = await answer(
        with_cap(
            plan(
                pub=public({"kind": "relate", "x": "enrollment", "y": "duration_months", "color_by": None}),
                rows=PointRows("enrollment", "duration_months", None),
            ),
            100,
        ),
        TRIALS,
    )

    for run in (listed, points):
        rows = run.response.visualization.data
        assert rows and all(len(trials_of(row)) == 1 and row.citation_count == 1 for row in rows)
        sound(run)
