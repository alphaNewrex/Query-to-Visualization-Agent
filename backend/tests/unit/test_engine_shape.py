"""Shaping: top-N never cuts a date axis, zero cells, order, series cap, network pruning, point cap."""

from collections.abc import Sequence

from ctviz.catalog.fields import Window
from ctviz.contract.response import StrategyStep
from ctviz.ctgov.study import Study
from ctviz.engine.aggregate import aggregate
from ctviz.engine.execute import EngineResult
from ctviz.engine.frame import Cell, Frame
from ctviz.engine.lower import EnginePlan, PointRows
from ctviz.engine.rows import trial_rows
from ctviz.engine.shape import MAX_POINTS, OTHER_KEY, shape

from .engine_support import COUNTRY, DRUG, PHASE, SPONSOR, START_DATE, bound, engine_plan, scope, study

STEP = StrategyStep(series=None, name="walk", reason="", upstream_requests=1)


def result_of(plan: EnginePlan, *trial_sets: Sequence[Study], window: Window | None = None) -> EngineResult:
    frames: list[Frame] = []
    for index, trials in enumerate(trial_sets):
        frame = aggregate(
            trials, plan, scope(f"g{index}", scope_id=f"s{index}"), {}, plan.citations_per_datum
        )
        frame.matched = frame.seen
        frames.append(frame)
    return EngineResult(tuple(frames), (), window or plan.window, (), (STEP,))


def keys_of(frame_cells: Sequence[Cell]) -> list[tuple[str, ...]]:
    return [cell.key for cell in frame_cells]


def test_top_n_never_cuts_a_date_axis() -> None:
    window = Window("year", "2015", "2019")
    plan = engine_plan(bound(START_DATE), window=window, top_n=2)
    trials = [study(n, start=f"{2015 + n % 5}-02") for n in range(1, 11)]

    shaped = shape(result_of(plan, trials), plan)

    assert keys_of(shaped.frames[0].cells) == [(str(year),) for year in range(2015, 2020)]
    assert shaped.truncation == ()


def test_leading_empty_periods_of_a_default_window_are_trimmed() -> None:
    window = Window("year", "2010", "2021")
    plan = engine_plan(bound(START_DATE), window=window)
    trials = [study(1, start="2019-02"), study(2, start="2021-06")]

    shaped = shape(result_of(plan, trials), plan)

    assert keys_of(shaped.frames[0].cells) == [("2019",), ("2020",), ("2021",)]
    assert shaped.window == Window("year", "2019", "2021")


def test_an_open_vocabulary_keeps_its_n_largest_and_a_multi_valued_one_has_no_other_row() -> None:
    plan = engine_plan(bound(COUNTRY), top_n=2)
    trials = [
        study(1, countries=["A", "B"]),
        study(2, countries=["A", "C"]),
        study(3, countries=["A", "B", "D"]),
    ]

    shaped = shape(result_of(plan, trials), plan)

    assert [(cell.key, cell.trials) for cell in shaped.frames[0].cells] == [(("A",), 3), (("B",), 2)]
    assert [(item.scope, item.shown, item.total) for item in shaped.truncation] == [("categories", 2, 4)]


def test_an_exclusive_dimension_sums_the_rest_into_an_other_row() -> None:
    plan = engine_plan(bound(SPONSOR), top_n=2)
    trials = [study(n, sponsor=name) for n, name in enumerate("AAABBCDE", start=1)]

    cells = shape(result_of(plan, trials), plan).frames[0].cells

    assert [(cell.key, cell.trials) for cell in cells] == [(("A",), 3), (("B",), 2), ((OTHER_KEY,), 3)]
    assert cells[-1].labels == ("Other (3 more)",)
    assert cells[-1].expr is None and cells[-1].source_url is None


def test_ordinal_buckets_keep_their_zero_cells_in_natural_order_and_nominal_ones_drop_them() -> None:
    trials = [study(1, phases=["PHASE2"])]
    ordinal = engine_plan(bound(PHASE))
    nominal = engine_plan(bound(SPONSOR))

    by_phase = shape(result_of(ordinal, trials), ordinal).frames[0].cells
    by_sponsor = shape(result_of(nominal, [study(1, sponsor="A")]), nominal).frames[0].cells

    assert [(cell.key, cell.trials) for cell in by_phase] == [
        (("PHASE1",), 0),
        (("PHASE2",), 1),
        (("NONE",), 0),
    ]
    assert by_phase[0].expr is not None  # a zero cell still says which expression selects it
    assert [cell.key for cell in by_sponsor] == [("A",)]


def test_compared_scopes_share_categories_in_the_order_of_their_combined_size() -> None:
    plan = engine_plan(bound(SPONSOR), top_n=15)
    first = [study(1, sponsor="A"), study(2, sponsor="B")]
    second = [study(3, sponsor="B"), study(4, sponsor="B"), study(5, sponsor="C")]

    shaped = shape(result_of(plan, first, second), plan)

    assert [keys_of(frame.cells) for frame in shaped.frames] == [[("B",), ("A",), ("C",)]] * 2
    assert [cell.trials for cell in shaped.frames[0].cells] == [1, 1, 0]


def test_a_second_dimension_gives_a_full_grid_and_an_exclusive_series_is_capped_with_other() -> None:
    window = Window("year", "2020", "2021")
    plan = engine_plan(bound(START_DATE), bound(SPONSOR, "series"), relation="series", window=window)
    trials = [study(n, start="2020-01", sponsor=f"S{n:02d}") for n in range(1, 13)]

    shaped = shape(result_of(plan, trials), plan)

    cells = shaped.frames[0].cells
    assert len(cells) == 2 * 10  # two periods by nine sponsors and Other
    assert [cell.key[1] for cell in cells[:10]][-1] == OTHER_KEY
    assert sum(cell.trials for cell in cells[:10]) == 12
    assert [(item.scope, item.shown, item.total) for item in shaped.truncation] == [("series", 9, 12)]


def test_the_share_of_a_cell_is_over_the_analysed_trials() -> None:
    plan = engine_plan(bound(SPONSOR))
    shaped = shape(
        result_of(plan, [study(1, sponsor="A"), study(2, sponsor="A"), study(3, sponsor="B")]), plan
    )

    assert shaped.frames[0].share(shaped.frames[0].cells[0]) == 2 / 3


# --- networks ---------------------------------------------------------------------------------------------


def network_plan() -> EnginePlan:
    return engine_plan(bound(DRUG, "node"), bound(DRUG, "node"), relation="network")


def test_network_pruning_drops_weak_links_and_nodes_left_without_one() -> None:
    plan = network_plan()
    trials = [
        study(1, drugs=[("A", []), ("B", []), ("C", [])]),
        study(2, drugs=[("A", []), ("B", [])]),
        study(3, drugs=[("A", []), ("C", [])]),
        study(4, drugs=[("B", []), ("C", [])]),
        study(5, drugs=[("A", []), ("D", [])]),
    ]

    shaped = shape(result_of(plan, trials), plan)

    frame = shaped.frames[0]
    assert [(cell.key, cell.trials) for cell in frame.cells] == [
        (("a", "b"), 2),
        (("a", "c"), 2),
        (("b", "c"), 2),
    ]
    assert [[node.key[0] for node in side] for side in frame.nodes] == [
        ["a", "b", "c"]
    ]  # D has only a weight-1 link
    assert {(item.scope, item.shown, item.total) for item in shaped.truncation} == {
        ("nodes", 3, 4),
        ("edges", 3, 4),
    }
    assert shaped.outcome is None


def test_network_minimum_weight_is_relaxed_once_and_a_sparse_graph_is_no_data() -> None:
    plan = network_plan()
    pairs = [study(n, drugs=[(f"A{n}", []), (f"B{n}", [])]) for n in range(1, 4)]

    relaxed = shape(result_of(plan, pairs), plan)
    sparse = shape(result_of(plan, pairs[:2]), plan)

    assert len(relaxed.frames[0].cells) == 3 and relaxed.outcome is None
    assert any("single trial" in sentence for sentence in relaxed.assumptions)
    assert sparse.outcome is not None and sparse.outcome.reason == "insufficient_cooccurrence"
    assert [note.code for note in sparse.warnings] == ["insufficient_cooccurrence"]


def test_a_drug_in_most_trials_of_a_drug_scope_is_left_out_as_the_anchor() -> None:
    from dataclasses import replace

    from ctviz.ctgov.params import BoundTerm

    term = BoundTerm("drug", "A", "A", "query.intr", None, "intervention_search", "")
    plan = network_plan()
    plan = replace(plan, scopes=(replace(plan.scopes[0], terms=(term,)),))
    trials = [study(n, drugs=[("A", []), ("B", []), ("C", []), ("D", [])]) for n in range(1, 4)]
    frame = aggregate(trials, plan, plan.scopes[0], {}, 2)
    frame.matched = 3

    shaped = shape(EngineResult((frame,), (), None, (), (STEP,)), plan)

    assert [cell.key for cell in shaped.frames[0].cells] == [("b", "c"), ("b", "d"), ("c", "d")]
    assert [note.code for note in shaped.warnings] == ["anchor_omitted"]
    assert "'A'" in shaped.assumptions[0]


# --- rows -------------------------------------------------------------------------------------------------


def test_scatter_points_are_capped_to_the_most_recently_first_posted() -> None:
    plan = engine_plan(rows=PointRows("enrollment", "site_count", None))
    trials = [
        study(n, enrollment=n, countries=["A"], posted=f"2020-{(n % 12) + 1:02d}-01")
        for n in range(1, MAX_POINTS + 2)
    ]
    rows = trial_rows(trials, plan, plan.scopes[0])
    result = EngineResult((), (rows,), None, (), (STEP,))

    shaped = shape(result, plan)

    kept = shaped.rows[0]
    assert len(kept.rows) == MAX_POINTS and kept.excluded["beyond_plotted_points"].count == 1
    assert [(item.scope, item.shown, item.total) for item in shaped.truncation] == [("points", 500, 501)]
    assert [note.code for note in shaped.warnings] == ["recent_subset"]


def test_compared_groups_share_a_split_that_drops_the_values_nobody_has() -> None:
    plan = engine_plan(bound(COUNTRY), bound(PHASE, "series"), relation="series")
    a = [study(1, countries=["A"], phases=("PHASE2",)), study(2, countries=["A"], phases=("PHASE2",))]
    b = [study(3, countries=["A"], phases=())]

    shaped = shape(result_of(plan, a, b), plan)

    assert [cell.key for cell in shaped.frames[0].cells] == [("A", "PHASE2"), ("A", "NONE")]
    assert [cell.key for cell in shaped.frames[1].cells] == [("A", "PHASE2"), ("A", "NONE")]
    assert [cell.trials for cell in shaped.frames[1].cells] == [0, 1]
