"""A walk and a count fan-out over the same records give the same frame, for the real catalogue.

Both executors fill one `Frame`. Whatever the strategy, a trial must be counted in the same cells, the
analysed total must be the same, and a trial left out must be left out for the same single reason (the
first that applies, in the order the walk checks them). The records are the 95 real pembrolizumab records
plus edge records that match two or three exclusion reasons at once.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

import pytest

from ctviz.catalog.fields import CATALOG, Window
from ctviz.contract.plan import QueryPlan
from ctviz.contract.request import QueryRequest, RequestOptions
from ctviz.ctgov import essie
from ctviz.ctgov.params import DateRange
from ctviz.ctgov.study import Study
from ctviz.engine.fanout import _probes, fan_out
from ctviz.engine.frame import Frame
from ctviz.engine.lower import Resolved, lower_plan
from ctviz.engine.strategy import ScopeRun
from ctviz.engine.walk import walk_frame

from .catalog_samples import pembrolizumab, withheld
from .engine_memory import (
    VERSION,
    Memory,
    Registry,
    answer,
    dim,
    execute,
    plan,
    public,
    record,
    rows_of,
    scope,
)

pytestmark = pytest.mark.anyio


@dataclass(frozen=True)
class Planned:
    plan: QueryPlan
    request: QueryRequest | None = None
    options: RequestOptions = field(default_factory=RequestOptions)


EDGES = [
    record(900, start="2020-02-29", enrollment=0, phases=()),
    record(901, start="2019-12-31", enrollment=1, phases=("PHASE2", "PHASE3")),
    record(902, start="2020-12", enrollment=9, phases=("PHASE1", "PHASE2")),
    record(903, start="2021-01-01", enrollment=10, phases=("EARLY_PHASE1",)),
    record(904, start="2024-03-31", enrollment=9999, phases=("NA",)),
    record(905, start="2024-04-01", enrollment=10000, phases=("PHASE4",)),
    record(906, start=None, enrollment=None, types=(), ages=()),
    # Each of these matches two or three reasons at once: before the window, no value of the second dimension.
    record(910, start="2015-02", types=(), ages=(), completion="2015-05"),
    record(911, start=None, types=(), ages=(), completion=None),
    record(912, start="2030-06", types=(), ages=("ADULT",), completion="2031-01"),
    record(913, start="2015-01", types=("DEVICE",), ages=(), completion="2016-01"),
]
STUDIES: Sequence[Study] = [*pembrolizumab(), withheld(), *EDGES]
YEARS = Window("year", "2017", "2026")
QUARTERS = Window("quarter", "2019-Q4", "2021-Q1")
MONTHS = Window("month", "2019-12", "2021-01")
DATES = ("start_date", "completion_date", "primary_completion_date", "first_posted_date")
CLOSED = [key for key, spec in CATALOG.items() if spec.buckets is not None and spec.kind != "date"]


def _cases() -> list[tuple[str, tuple[str, ...], Window | None]]:
    cases: list[tuple[str, tuple[str, ...], Window | None]] = [(key, (key,), None) for key in CLOSED]
    for key in DATES:
        for window in (YEARS, QUARTERS, MONTHS):
            cases.append((f"{key}/{window.unit}", (key,), window))
    cases += [
        ("start x phase", ("start_date", "phase"), YEARS),
        ("start x intervention type (multi-valued)", ("start_date", "intervention_type"), YEARS),
        ("start x age group (multi-valued)", ("start_date", "age_group"), QUARTERS),
        ("completion x intervention type", ("completion_date", "intervention_type"), MONTHS),
        ("phase x status", ("phase", "overall_status"), None),
        ("phase x age group (multi-valued)", ("phase", "age_group"), None),
        ("enrollment x sex", ("enrollment", "sex"), None),
        ("enrollment x age group (multi-valued)", ("enrollment", "age_group"), None),
        ("age group x sex", ("age_group", "sex"), None),
        ("intervention type x age group (both multi-valued)", ("intervention_type", "age_group"), None),
    ]
    return cases


async def _both(
    keys: tuple[str, ...], window: Window | None, *, date_range: DateRange | None = None
) -> tuple[Frame, Frame]:
    unit = window.unit if window is not None else None
    dims = tuple(
        dim(key, "axis" if position == 0 else "series", unit if position == 0 else None)
        for position, key in enumerate(keys)
    )
    pub = public({"kind": "total", "statistic": None, "of": None})
    one = scope(date_range=date_range)
    p = plan(*dims, pub=pub, scopes=(one,), relation="series" if len(dims) > 1 else None, window=window)
    client, ctx = Registry(STUDIES), Memory()
    matched = (await client.walk(one.params(), ctx, fields=())).total
    run = ScopeRun(one, matched, "walk", "", ("NCTId",), 1000)
    walked = await walk_frame(run, p, window, client, ctx)
    counted = await fan_out(one, p, matched=matched, window=window, client=client, ctx=ctx)
    return walked, counted


def _summary(frame: Frame) -> dict[str, object]:
    return {
        "cells": {key: cell.trials for key, cell in frame.cells.items() if cell.trials},
        "matched": frame.matched,
        "analyzed": frame.analyzed,
        "excluded": {reason: item.count for reason, item in frame.excluded.items() if item.count},
        "warnings": [note.code for note in frame.warnings],
    }


@pytest.mark.parametrize(("name", "keys", "window"), _cases(), ids=[case[0] for case in _cases()])
async def test_walk_and_fan_out_give_the_same_frame(
    name: str, keys: tuple[str, ...], window: Window | None
) -> None:
    walked, counted = await _both(keys, window)

    assert _summary(counted) == _summary(walked), name
    for position, table in enumerate(counted.marginals):
        # A fan-out has the marginals of a dimension only where the other dimension is exclusive.
        for key, cell in table.items():
            found = walked.marginals[position].get(key)
            assert (found.trials if found else 0) == cell.trials, (name, key)


async def test_a_trial_that_fits_two_reasons_is_left_out_once() -> None:
    walked, counted = await _both(("start_date", "intervention_type"), YEARS)

    assert _summary(counted)["excluded"] == _summary(walked)["excluded"]
    # 910 and 913 started before the window; 910 also lists no intervention type, 911 has neither a start
    # date nor a type. Each is one exclusion, the first that applies.
    for frame in (walked, counted):
        reasons = {reason: item.count for reason, item in frame.excluded.items()}
        assert frame.analyzed >= 0
        assert frame.analyzed + sum(reasons.values()) == frame.matched
        assert reasons["no_start_date"] >= 1 and reasons["before_window"] >= 2


async def test_the_probes_are_disjoint_in_the_order_the_walk_excludes() -> None:
    dims = (dim("start_date"), dim("intervention_type", "series"))
    p = plan(
        *dims, pub=public({"kind": "total", "statistic": None, "of": None}), relation="series", window=YEARS
    )

    probes = _probes(p, p.scopes[0], YEARS)

    assert [reason for reason, _, _ in probes] == [
        "no_start_date",
        "before_window",
        "after_window",
        "no_intervention_type",
    ]
    # No trial satisfies two probes.
    client = Registry(STUDIES)
    ctx = Memory()
    seen: dict[str, str] = {}
    for reason, _, expr in probes:
        page = await client.walk(p.scopes[0].params().narrowed_by(expr), ctx, fields=())
        for study in page.studies:
            assert study.nct_id not in seen, (study.nct_id, seen[study.nct_id], reason)
            seen[study.nct_id] = reason


async def test_a_chart_with_data_does_not_become_no_data_because_of_double_subtraction() -> None:
    # Six trials, most of which match two reasons: summed independently the probes remove more than exist.
    trials = [
        record(1, start="2020-03", types=("DRUG",)),
        record(2, start="2015-01", types=()),
        record(3, start="2015-02", types=()),
        record(4, start=None, types=()),
        record(5, start="2015-03", types=()),
        record(6, start="2016-03", types=()),
    ]
    one = scope()
    pub = public({"kind": "total", "statistic": None, "of": None})
    p = plan(dim("start_date"), dim("intervention_type", "series"), pub=pub, relation="series", window=YEARS)
    client, ctx = Registry(trials), Memory()

    frame = await fan_out(one, p, matched=6, window=YEARS, client=client, ctx=ctx)

    assert frame.analyzed == 1
    assert {reason: item.count for reason, item in frame.excluded.items()} == {
        "no_start_date": 1,
        "before_window": 4,
    }


async def test_a_date_range_on_the_axis_field_gives_the_same_frame_in_both_paths() -> None:
    date_range = DateRange("StartDate", "2019-01-01", "2023-12-31")

    walked, counted = await _both(("start_date", "phase"), YEARS, date_range=date_range)

    assert _summary(counted) == _summary(walked)


def test_the_cascade_leaves_out_each_earlier_probe() -> None:
    p = plan(dim("start_date"), pub=public({"kind": "total", "statistic": None, "of": None}), window=YEARS)

    (missing, before, after) = _probes(p, p.scopes[0], YEARS)

    assert missing[2] == essie.missing("StartDate")
    assert str(before[2]).endswith(f"AND (NOT ({essie.missing('StartDate')}))")
    assert str(after[2]).count("NOT (") == 2


PUSHDOWN_EDGES = [
    record(920, start="2012-01", countries=()),  # before the window, and no country
    record(921, start=None, countries=()),  # no start date, and no country
    record(922, start="2020-05", countries=()),  # no country only
    record(923, start="2031-05", countries=("France",)),  # after the window
    record(924, start="2031-06", countries=()),  # after the window, and no country
]


@pytest.mark.parametrize(
    "keys", [("start_date", "country"), ("country", "drug"), ("drug", "country"), ("country", "condition")]
)
async def test_a_walk_with_a_push_down_gives_the_reasons_of_a_walk_without_one(keys: tuple[str, ...]) -> None:
    studies = [*pembrolizumab(), *PUSHDOWN_EDGES]
    window = Window("year", "2017", "2026")
    dims = tuple(dim(key, "axis" if position == 0 else "series") for position, key in enumerate(keys))
    pub = public({"kind": "total", "statistic": None, "of": None})
    p = plan(*dims, pub=pub, relation="series", window=window if keys[0] == "start_date" else None)

    _, whole, _, _ = await execute(p, studies, one_page_max=1000, prefer_walk=True)
    plan_of_pushed, pushed, _, _ = await execute(p, studies, one_page_max=10, prefer_walk=True)

    assert [run.strategy for run in plan_of_pushed.runs] == ["walk"]
    assert plan_of_pushed.runs[0].walk_scope is not None
    one, two = whole.frames[0], pushed.frames[0]
    assert two.matched == one.matched == len(studies)
    assert {k: v.count for k, v in two.excluded.items() if v.count} == {
        k: v.count for k, v in one.excluded.items() if v.count
    }
    assert two.analyzed == one.analyzed


async def test_trials_that_started_in_a_year_are_shown_by_the_years_they_completed_in() -> None:
    trials = [
        record(1, start="2020-02", completion="2020-11"),
        record(2, start="2020-03", completion="2021-06"),
        record(3, start="2020-05", completion="2022-01"),
        record(4, start="2020-07", completion="2023-09"),
        record(5, start="2019-01", completion="2020-05"),  # started in 2019: not in the scope
    ]
    analysis = {
        "kind": "aggregate",
        "dimension": "completion_date",
        "series": None,
        "time_unit": "year",
        "top_n": None,
        "statistic": None,
        "of": None,
    }
    pub = public(analysis, date_field="start_date", year_from=2020, year_to=2020)
    one = scope(date_range=DateRange("StartDate", "2020-01-01", "2020-12-31"))
    lowered = lower_plan(Planned(pub), Resolved((), (one,), {"s0": 4}, (), ()), CATALOG, VERSION)

    run = await answer(lowered, trials)

    shown = {row["completion_year"]: row["trial_count"] for row in rows_of(run.response)}
    # The axis is the default window, not the one year the trials started in.
    assert [year for year, n in shown.items() if n] == ["2020", "2021", "2022", "2023"]
    assert shown["2023"] == 1 and "2026" in shown
    counts = run.response.meta.counts
    assert counts is not None and counts.series[0].trials_excluded == []
