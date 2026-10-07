"""The overlap of compared groups: one count of the trials in both, never fatal to the chart."""

from collections.abc import Sequence
from typing import Any

import pytest

from ctviz.contract.response import Note
from ctviz.ctgov.params import QUERY_PARAMETERS, BoundTerm, Params, Scope
from ctviz.ctgov.study import Study
from ctviz.engine.overlap import Overlap, _both, shared_trials
from ctviz.errors import AppError, DeadlineExceeded

from .engine_memory import Memory, Registry, answer, plan, public, record, rows_of, scope

pytestmark = pytest.mark.anyio


def drug(name: str) -> BoundTerm:
    return BoundTerm(
        kind="drug",
        text=name,
        term=name,
        parameter="query.intr",
        expr=None,
        definition="intervention_search",
        note="",
    )


def scopes(
    *names: str,
    statuses: Sequence[str] = ("RECRUITING", "NOT_YET_RECRUITING"),
    phases: Sequence[str] = ("PHASE3",),
) -> tuple[Scope, ...]:
    filters: dict[str, tuple[str, ...]] = {}
    if statuses:
        filters["overall_status"] = tuple(statuses)
    if phases:
        filters["phase"] = tuple(phases)
    return tuple(
        scope(name, f"s{n}", terms=(drug(name),), enum_filters=filters) for n, name in enumerate(names)
    )


def trial(number: int, *drugs: str, status: str = "RECRUITING", phase: str = "PHASE3") -> Study:
    return record(number, names=drugs, types=("DRUG",) * len(drugs), status=status, phases=(phase,))


TRIALS = [
    *(trial(n, "pembrolizumab") for n in (1, 2, 3)),
    *(trial(n, "nivolumab") for n in (4, 5)),
    *(trial(n, "pembrolizumab", "nivolumab") for n in (6, 7)),
    trial(8, "pembrolizumab", "nivolumab", status="COMPLETED"),  # both drugs, outside the status filter
    trial(9, "pembrolizumab", "nivolumab", phase="PHASE2"),  # both drugs, outside the phase filter
    trial(10, "atezolizumab"),
]
TOTAL = public({"kind": "total", "statistic": None, "of": None})


def test_two_searches_on_one_parameter_become_one_and_and_a_shared_list_stays_once() -> None:
    first, second = (s.params() for s in scopes("pembrolizumab", "nivolumab"))

    both = _both(first, second)

    assert both is not None
    pairs = dict(both.pairs())
    assert pairs["query.intr"] == "(pembrolizumab) AND (nivolumab)"
    # The registry takes one list of statuses: an AND of two lists is a 400.
    assert pairs["filter.overallStatus"] == "RECRUITING|NOT_YET_RECRUITING"
    assert pairs["filter.advanced"] == "AREA[Phase]PHASE3"


def test_only_the_query_parameters_are_joined_with_and() -> None:
    first = Params((("query.cond", "lung cancer"), ("query.intr", "a"), ("filter.overallStatus", "A|B")))
    second = Params((("query.cond", "lung cancer"), ("query.intr", "b"), ("filter.overallStatus", "B|C")))

    both = _both(first, second)

    assert both is not None
    pairs = dict(both.texts)
    assert pairs["query.cond"] == "lung cancer"  # the same search on both sides is not repeated
    assert pairs["query.intr"] == "(a) AND (b)"
    assert pairs["filter.overallStatus"] == "B"  # a trial has one status: both lists must hold it
    assert all(" AND " not in value for name, value in both.texts if name not in QUERY_PARAMETERS)


def test_lists_with_no_status_in_common_select_nothing() -> None:
    first = Params((("filter.overallStatus", "RECRUITING"),))
    second = Params((("filter.overallStatus", "COMPLETED"),))

    assert _both(first, second) is None


async def test_the_overlap_counts_the_trials_of_both_groups_under_the_shared_filters() -> None:
    a, b = scopes("pembrolizumab", "nivolumab")
    client, ctx = Registry(TRIALS), Memory()
    matched = {"s0": 6, "s1": 4}  # any non-zero count says the groups are not empty

    overlap = await shared_trials(
        plan(pub=TOTAL, scopes=(a, b)),
        matched,
        client,
        ctx,
    )

    assert overlap == Overlap(2)  # trials 6 and 7 only: 8 is completed and 9 is Phase 2


async def test_groups_whose_filters_exclude_each_other_overlap_nowhere_and_cost_no_request() -> None:
    a, b = scopes("pembrolizumab", "nivolumab")
    b = Scope(b.id, b.label, b.terms, {"overall_status": ("COMPLETED",)}, None)
    client, ctx = Registry(TRIALS), Memory()
    p = plan(pub=TOTAL, scopes=(a, b))

    overlap = await shared_trials(p, {"s0": 1, "s1": 1}, client, ctx)

    assert overlap == Overlap(0) and ctx.requests == []


async def test_three_to_five_groups_have_no_overlap_count() -> None:
    three = scopes("pembrolizumab", "nivolumab", "atezolizumab")
    p = plan(pub=TOTAL, scopes=three)
    ctx = Memory()

    overlap = await shared_trials(p, {s.id: 1 for s in three}, Registry(TRIALS), ctx)

    assert overlap == Overlap(None) and ctx.requests == []


async def test_an_empty_group_has_no_overlap() -> None:
    a, b = scopes("pembrolizumab", "nivolumab")
    p = plan(pub=TOTAL, scopes=(a, b))

    assert await shared_trials(p, {"s0": 5, "s1": 0}, Registry(TRIALS), Memory()) == Overlap(None)


class _Failing(Registry):
    def __init__(self, studies: Sequence[Any], error: Exception) -> None:
        super().__init__(studies)
        self.error, self.armed = error, False

    async def count(self, params: Params, ctx: Any, *, origin: Any) -> int:
        if self.armed:
            raise self.error
        return await super().count(params, ctx, origin=origin)


async def test_a_failed_overlap_count_is_a_warning_and_not_an_error() -> None:
    a, b = scopes("pembrolizumab", "nivolumab")
    p = plan(pub=TOTAL, scopes=(a, b))
    client = _Failing(TRIALS, AppError("ClinicalTrials.gov rejected a query that this service built."))
    client.armed = True

    overlap = await shared_trials(p, {"s0": 6, "s1": 4}, client, Memory())

    assert overlap.shared is None
    assert [note.code for note in overlap.warnings] == ["overlap_not_counted"]
    assert isinstance(overlap.warnings[0], Note)


async def test_running_out_of_time_is_still_an_error() -> None:
    a, b = scopes("pembrolizumab", "nivolumab")
    p = plan(pub=TOTAL, scopes=(a, b))
    client = _Failing(TRIALS, DeadlineExceeded("late"))
    client.armed = True

    with pytest.raises(DeadlineExceeded):
        await shared_trials(p, {"s0": 6, "s1": 4}, client, Memory())


async def test_the_chart_of_two_groups_with_a_shared_status_filter_reports_the_overlap() -> None:
    pub = TOTAL
    p = plan(pub=pub, scopes=scopes("pembrolizumab", "nivolumab"), compare_kind="drug")

    run = await answer(p, TRIALS)

    counts = run.response.meta.counts
    assert counts is not None and counts.trials_in_several_series == 2
    assert [row["trial_count"] for row in rows_of(run.response)] == [5, 4]
    assert any("2 trials involve both" in note for note in run.response.meta.assumptions)
    assert not any("overlap" in warning.code for warning in run.response.meta.warnings)


async def test_the_chart_is_returned_with_a_warning_when_the_overlap_cannot_be_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pub = TOTAL
    p = plan(pub=pub, scopes=scopes("pembrolizumab", "nivolumab"), compare_kind="drug")
    original = Registry.count

    async def reject_the_joined_search(self: Registry, params: Params, ctx: Any, *, origin: Any) -> int:
        if any(" AND " in value for name, value in params.texts if name == "query.intr"):
            raise AppError("ClinicalTrials.gov rejected a query that this service built.")
        return await original(self, params, ctx, origin=origin)

    monkeypatch.setattr(Registry, "count", reject_the_joined_search)

    run = await answer(p, TRIALS)

    assert [row["trial_count"] for row in rows_of(run.response)] == [5, 4]
    assert run.response.meta.counts is not None
    assert run.response.meta.counts.trials_in_several_series is None
    assert "overlap_not_counted" in [warning.code for warning in run.response.meta.warnings]


async def test_three_compared_groups_say_that_their_overlap_is_not_counted() -> None:
    pub = TOTAL
    p = plan(pub=pub, scopes=scopes("pembrolizumab", "nivolumab", "atezolizumab"), compare_kind="drug")

    run = await answer(p, TRIALS)

    assert run.response.meta.counts is not None
    assert run.response.meta.counts.trials_in_several_series is None
    assert any("3 compared groups" in note for note in run.response.meta.assumptions)
