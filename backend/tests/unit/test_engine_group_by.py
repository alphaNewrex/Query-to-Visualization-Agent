"""The group-by and its two executors: a walk and a fan-out fill the same frame."""

from collections.abc import Sequence

import pytest

from ctviz.catalog.fields import BoundDimension, Value, Window
from ctviz.ctgov import essie
from ctviz.ctgov.client import CtGovClient
from ctviz.ctgov.study import Study
from ctviz.engine import windows
from ctviz.engine.aggregate import aggregate, cells
from ctviz.engine.execute import execute_plan
from ctviz.engine.fanout import fan_out
from ctviz.engine.frame import Frame
from ctviz.engine.registry import Registry
from ctviz.engine.strategy import ExecutionPlan, ScopeRun
from ctviz.engine.walk import walk_frame
from ctviz.settings import Settings

from .engine_support import (
    COUNTRY,
    DRUG,
    PHASE,
    SPONSOR,
    START_DATE,
    FakeClient,
    FakeContext,
    bound,
    engine_plan,
    scope,
    study,
)

pytestmark = pytest.mark.anyio

TRIALS = [
    study(1, phases=["PHASE1"], start="2019-05"),
    study(2, phases=["PHASE1"], start="2020-01"),
    study(3, phases=["PHASE2"], start="2020-07"),
    study(4, phases=["PHASE2"], start="2021-02"),
    study(5, phases=[], start="2021-09"),
    study(6, phases=["PHASE1"], start=None),
    study(7, phases=["PHASE2"], start="2015-01"),
]
WINDOW = Window("year", "2019", "2021")


def counts(frame: Frame) -> dict[tuple[str, ...], int]:
    return {key: cell.trials for key, cell in frame.cells.items()}


async def both_ways(
    plan_dims: tuple[BoundDimension, ...], window: Window | None, studies: Sequence[Study] = TRIALS
) -> tuple[Frame, Frame]:
    plan = engine_plan(*plan_dims, window=window)
    client, ctx = FakeClient(studies), FakeContext()
    run = ScopeRun(plan.scopes[0], len(studies), "walk", "", ("NCTId",), 1000)
    walked = await walk_frame(run, plan, window, client, ctx)
    counted = await fan_out(plan.scopes[0], plan, matched=len(studies), window=window, client=client, ctx=ctx)
    return walked, counted


async def test_a_closed_category_gives_the_same_frame_by_walk_and_by_fan_out() -> None:
    walked, counted = await both_ways((bound(PHASE),), None)

    assert counts(walked) == counts(counted) == {("PHASE1",): 3, ("PHASE2",): 3, ("NONE",): 1}
    assert walked.analyzed == counted.analyzed == 7
    assert walked.excluded == counted.excluded == {}


async def test_a_date_axis_gives_the_same_frame_by_walk_and_by_fan_out() -> None:
    walked, counted = await both_ways((bound(START_DATE),), WINDOW)

    expected = {("2019",): 1, ("2020",): 2, ("2021",): 2}
    assert {key: n for key, n in counts(walked).items()} == expected
    assert counts(counted) == expected
    # One trial has no start date and one started in 2015, before the window.
    for frame in (walked, counted):
        assert {reason: item.count for reason, item in frame.excluded.items()} == {
            "no_start_date": 1,
            "before_window": 1,
        }
        assert frame.analyzed == 5
        assert frame.matched == 7


async def test_two_closed_dimensions_give_the_same_grid_by_walk_and_by_fan_out() -> None:
    walked, counted = await both_ways((bound(START_DATE), bound(PHASE, "series")), WINDOW)

    assert counts(walked) == {key: n for key, n in counts(counted).items() if n}
    assert counts(counted)[("2020", "PHASE2")] == 1
    assert counts(counted)[("2019", "PHASE2")] == 0  # the fan-out counts every cell, even an empty one


async def test_a_fan_out_cell_cites_trials_that_have_the_bucket_and_names_its_url() -> None:
    _, counted = await both_ways((bound(PHASE),), None)

    cell = counted.cells[("PHASE1",)]
    assert {trial.nct_id for trial in cell.sample} <= {"NCT00000001", "NCT00000002", "NCT00000006"}
    assert len(cell.sample) == 2  # the plan's citations per datum
    assert cell.sample[0].evidence[0].excerpt == "PHASE1"
    assert cell.source_url is not None and "AREA%5BPhase%5DPHASE1" in cell.source_url
    assert cell.expr == essie.area("Phase", "PHASE1")


async def test_partition_checksum_warns_and_reconciles_when_the_counts_do_not_add_up() -> None:
    plan = engine_plan(bound(PHASE))
    client = FakeClient(TRIALS, miscount={"AREA[Phase]PHASE2": 4})

    frame = await fan_out(plan.scopes[0], plan, matched=7, window=None, client=client, ctx=FakeContext())

    assert [note.code for note in frame.warnings] == ["counts_not_reconciled"]
    assert frame.matched == 11  # the sum actually counted
    assert frame.analyzed == 11


async def test_a_walk_reads_every_trial_and_leaves_none_outside() -> None:
    many = [study(number, posted=f"2024-01-{number:02d}") for number in range(1, 13)]
    plan = engine_plan(bound(PHASE))
    run = ScopeRun(plan.scopes[0], 12, "walk", "", ("NCTId",))

    frame = await walk_frame(run, plan, None, FakeClient(many), FakeContext())

    assert (frame.analyzed, frame.matched, frame.excluded) == (12, 12, {})
    assert frame.warnings == []


async def test_a_walk_that_reads_fewer_trials_than_counted_says_so() -> None:
    many = [study(number) for number in range(1, 13)]
    plan = engine_plan(bound(PHASE))
    run = ScopeRun(plan.scopes[0], 12, "walk", "", ("NCTId",))

    frame = await walk_frame(run, plan, None, FakeClient(many, miscount={"": 3}), FakeContext())

    assert [note.code for note in frame.warnings] == ["walk_count_mismatch"]
    assert frame.analyzed == frame.matched == 12  # matched is what was read: the identity still holds


async def test_a_full_walk_composes_the_url_a_fan_out_would_have_called() -> None:
    walked, counted = await both_ways((bound(PHASE),), None)

    for key, cell in walked.cells.items():
        url, called = cell.source_url, counted.cells[key].source_url
        assert url is not None and called is not None
        assert url.startswith("https://clinicaltrials.gov/api/v2/studies?")
        assert url.split("/studies?")[1] == called.split("/studies?")[1]


async def test_a_walk_leaves_the_url_out_where_no_search_selects_the_cell() -> None:
    trials = [study(number, drugs=[("Aspirin", ["A"])]) for number in range(1, 8)]
    by_drug = engine_plan(bound(DRUG))
    full = ScopeRun(by_drug.scopes[0], 7, "walk", "", ("NCTId",), 1000)
    by_phase = engine_plan(bound(PHASE))
    capped = ScopeRun(by_phase.scopes[0], 7, "capped_walk", "", ("NCTId",), 5, "StudyFirstPostDate:desc")

    free_text = await walk_frame(full, by_drug, None, FakeClient(trials), FakeContext())
    subset = await walk_frame(capped, by_phase, None, FakeClient(trials), FakeContext())

    assert free_text.cells and subset.cells
    assert all(cell.source_url is None for cell in [*free_text.cells.values(), *subset.cells.values()])


async def test_compared_scopes_are_series_and_an_empty_group_is_a_zero_frame() -> None:
    trials = [study(1, sponsor="Acme"), study(2, sponsor="Acme"), study(3, sponsor="Beta")]
    scopes = (scope("Acme", sponsor="Acme"), scope("Beta", sponsor="Beta", scope_id="s1"))
    plan = engine_plan(bound(PHASE), scopes=scopes)
    runs = tuple(ScopeRun(s, 2 - i, "walk", "reason", ("NCTId",), 1000) for i, s in enumerate(scopes))
    runs += (ScopeRun(scope("Gone", scope_id="s2"), 0, "none", "empty"),)

    result = await execute_plan(plan, ExecutionPlan(runs, None, ()), FakeClient(trials), FakeContext())

    assert [counts(frame) for frame in result.frames] == [{("PHASE1",): 2}, {("PHASE1",): 1}, {}]
    assert [step.upstream_requests for step in result.steps] == [1, 1, 0]
    assert [step.series for step in result.steps] == ["Acme", "Beta", "Gone"]


# --- cells ------------------------------------------------------------------------------------------------


def test_cells_of_no_dimension_is_one_empty_cell() -> None:
    assert list(cells([], [], "same_trial")) == [()]


def test_cells_of_two_different_dimensions_is_the_cross_product() -> None:
    dims = [bound(PHASE), bound(COUNTRY, "series")]
    a, b, x = Value("A", "A", ()), Value("B", "B", ()), Value("X", "X", ())
    assert list(cells([[a, b], [x]], dims, "same_trial")) == [(a, x), (b, x)]


def test_cells_of_one_dimension_twice_are_unordered_pairs_and_same_arm_needs_a_shared_arm() -> None:
    dims = [bound(DRUG, "node"), bound(DRUG, "node")]
    a = Value("a", "A", (), frozenset({"arm1"}))
    b = Value("b", "B", (), frozenset({"arm1", "arm2"}))
    c = Value("c", "C", (), frozenset({"arm3"}))

    by_trial = [(x.key, y.key) for x, y in cells([[c, a, b]] * 1, dims, "same_trial")]
    by_arm = [(x.key, y.key) for x, y in cells([[c, a, b]], dims, "same_arm")]

    assert by_trial == [("a", "b"), ("a", "c"), ("b", "c")]
    assert by_arm == [("a", "b")]


async def test_a_network_frame_counts_each_pair_once_per_trial_and_node_sizes_per_trial() -> None:
    trials = [
        study(1, drugs=[("Alpha", ["x"]), ("Beta", ["x"]), ("Gamma", ["y"])]),
        study(2, drugs=[("Alpha", ["x"]), ("Beta", ["x"])]),
        study(3, drugs=[("Alpha", ["x"]), ("Alpha", ["x"])]),  # one drug twice: no pair, one node count
    ]
    plan = engine_plan(bound(DRUG, "node"), bound(DRUG, "node"), relation="network", pairing="same_arm")

    frame = aggregate(trials, plan, plan.scopes[0], {}, 2)

    assert counts(frame) == {("alpha", "beta"): 2}
    assert {key: cell.trials for key, cell in frame.marginals[0].items()} == {
        "alpha": 3,
        "beta": 2,
        "gamma": 1,
    }
    assert frame.marginals[0] is frame.marginals[1]


async def test_two_different_kinds_of_node_form_a_bipartite_frame() -> None:
    trials = [
        study(1, sponsor="Acme", drugs=[("Alpha", [])]),
        study(2, sponsor="Acme", drugs=[("Alpha", [])]),
    ]
    plan = engine_plan(bound(SPONSOR, "node"), bound(DRUG, "node"), relation="network")

    frame = aggregate(trials, plan, plan.scopes[0], {}, 2)

    assert counts(frame) == {("Acme", "alpha"): 2}
    assert [list(table) for table in frame.marginals] == [["Acme"], ["alpha"]]


def test_windows_end_where_asked_and_the_latest_periods_are_the_last_ones() -> None:
    assert windows.ending_at("year", "2026", 25) == Window("year", "2002", "2026")
    assert windows.ending_at("quarter", "2026-Q4", 6) == Window("quarter", "2025-Q3", "2026-Q4")
    assert windows.latest(Window("month", "2024-01", "2024-12"), 3) == Window("month", "2024-10", "2024-12")
    assert windows.latest(WINDOW, 10) == WINDOW


async def test_the_real_client_has_the_shape_the_engine_asks_for() -> None:
    client = CtGovClient(Settings())
    registry: Registry = client  # mypy compares the two shapes
    await client.aclose()
    assert registry is client
