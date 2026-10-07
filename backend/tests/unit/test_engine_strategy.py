"""The strategy table of section 4.9, row by row."""

from dataclasses import replace

from ctviz.catalog.fields import Window
from ctviz.contract.response import Outcome
from ctviz.ctgov import essie
from ctviz.ctgov.params import DateRange
from ctviz.engine.fanout import fan_out_bill
from ctviz.engine.lower import EnginePlan, ListRows, PointRows
from ctviz.engine.strategy import ExecutionPlan, Limits, choose_strategy

from .engine_support import COUNTRY, DRUG, PHASE, START_DATE, bound, engine_plan, scope

LIMITS = Limits(one_page_max=1000, walk_cap=5000, max_fanout_requests=60)


def chosen(
    plan: EnginePlan, matched: dict[str, int], limits: Limits = LIMITS, *, prefer_walk: bool = False
) -> ExecutionPlan:
    answer = choose_strategy(plan, matched, limits, prefer_walk=prefer_walk)
    assert isinstance(answer, ExecutionPlan), answer
    return answer


def test_a_question_that_matches_nothing_is_no_data() -> None:
    answer = choose_strategy(engine_plan(bound(PHASE)), {"s0": 0}, LIMITS, prefer_walk=False)

    assert isinstance(answer, Outcome) and (answer.kind, answer.reason) == ("no_data", "no_trials_matched")


def test_an_empty_compared_group_stays_as_an_empty_series_with_a_warning() -> None:
    scopes = (scope("A", scope_id="s0"), scope("B", scope_id="s1"))

    xp = chosen(engine_plan(bound(PHASE), scopes=scopes), {"s0": 40, "s1": 0})

    assert [run.strategy for run in xp.runs] == ["walk", "none"]
    assert [note.code for note in xp.warnings] == ["series_matched_nothing"]


def test_a_trial_list_is_one_sorted_page() -> None:
    plan = engine_plan(rows=ListRows("enrollment", "desc"), top_n=10)

    run = chosen(plan, {"s0": 50_000}).runs[0]

    assert (run.strategy, run.limit, run.sort) == ("sorted_page", 10, "EnrollmentCount:desc")
    assert "EnrollmentCount" in run.fields


def test_a_single_number_is_one_sample_call() -> None:
    run = chosen(engine_plan(), {"s0": 500_000}).runs[0]

    assert run.strategy == "count_fan_out"


def test_a_scope_that_fits_one_page_is_walked_once() -> None:
    run = chosen(engine_plan(bound(COUNTRY)), {"s0": 999}).runs[0]

    assert (run.strategy, run.limit, run.walk_scope) == ("walk", 1000, None)
    assert run.fields[0] == "NCTId" and "LocationCountry" in run.fields


def test_closed_buckets_above_one_page_are_counted_not_walked() -> None:
    xp = chosen(engine_plan(bound(PHASE)), {"s0": 3000})

    assert xp.runs[0].strategy == "count_fan_out"


def test_a_throttled_registry_walks_before_it_fans_out() -> None:
    run = chosen(engine_plan(bound(PHASE)), {"s0": 3000}, prefer_walk=True).runs[0]

    assert (run.strategy, run.limit) == ("walk", 5000)


def test_an_open_vocabulary_above_one_page_is_walked_with_a_presence_push_down() -> None:
    presence = essie.not_(essie.missing("LocationCountry"))
    country = replace(COUNTRY, presence=presence)

    run = chosen(engine_plan(bound(country)), {"s0": 3000}).runs[0]

    assert run.strategy == "walk" and run.walk_scope is not None
    assert run.walk_scope.extra == (presence,)
    assert run.scope.extra == ()


def test_above_the_cap_an_open_vocabulary_reads_the_newest_trials_and_says_so() -> None:
    run = chosen(engine_plan(bound(COUNTRY)), {"s0": 9000}).runs[0]

    assert (run.strategy, run.limit, run.sort) == ("capped_walk", 5000, "StudyFirstPostDate:desc")


def test_a_drug_network_above_the_cap_also_needs_two_interventions() -> None:
    drug = replace(DRUG, presence=essie.area("InterventionType", "DRUG"))
    plan = engine_plan(bound(drug, "node"), bound(drug, "node"), relation="network")

    run = chosen(plan, {"s0": 9000}).runs[0]

    assert run.strategy == "capped_walk"
    assert run.walk_scope is not None and len(run.walk_scope.extra) == 2
    assert "Intervention:size" in run.walk_scope.extra[1]


def test_scatter_points_above_the_cap_are_a_recent_subset() -> None:
    plan = engine_plan(rows=PointRows("enrollment", "site_count", None))

    assert chosen(plan, {"s0": 9000}).runs[0].strategy == "capped_walk"
    assert chosen(plan, {"s0": 3000}).runs[0].strategy == "walk"


def test_a_date_axis_never_comes_from_a_subset_and_its_window_is_shortened_to_fit_the_bill() -> None:
    window = Window("year", "1990", "2014")
    plan = engine_plan(bound(START_DATE), bound(PHASE, "series"), relation="series", window=window)

    xp = chosen(plan, {"s0": 9000})

    # Three phase buckets and three extra counts: the longest window with 3 * periods + 3 <= 60 is 19.
    assert xp.runs[0].strategy == "count_fan_out"
    assert xp.window == Window("year", "1996", "2014")
    assert [note.code for note in xp.warnings] == ["window_clamped"]


def test_a_date_axis_that_cannot_be_counted_even_for_five_periods_asks_for_a_narrower_question() -> None:
    plan = engine_plan(bound(START_DATE), window=Window("year", "1990", "2014"))

    answer = choose_strategy(plan, {"s0": 9000}, Limits(1000, 5000, 7), prefer_walk=False)

    assert isinstance(answer, Outcome) and (answer.kind, answer.reason) == ("clarification", "too_broad")


def test_a_window_above_sixty_periods_is_cut_for_a_walk() -> None:
    plan = engine_plan(bound(START_DATE), window=Window("year", "1900", "2026"))

    xp = chosen(plan, {"s0": 500})

    assert xp.window == Window("year", "1967", "2026")
    assert [note.code for note in xp.warnings] == ["window_clamped"]


def test_the_fan_out_bill_includes_the_counts_of_trials_outside_the_window() -> None:
    window = Window("year", "2020", "2022")
    plan = engine_plan(bound(START_DATE), window=window)
    limited = replace(plan.scopes[0], date_range=DateRange("StartDate", "2020-01-01", "2022-12-31"))

    assert fan_out_bill(plan, window) == 3 + 3  # before, after, no start date
    # A scope limited to the window's years has nothing before it, after it or without a date.
    assert fan_out_bill(replace(plan, scopes=(limited,)), window) == 3
