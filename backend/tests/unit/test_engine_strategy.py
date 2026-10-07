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

LIMITS = Limits(one_page_max=1000, max_fanout_requests=60)


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

    assert (run.strategy, run.walk_scope) == ("walk", None)
    assert run.fields[0] == "NCTId" and "LocationCountry" in run.fields


def test_closed_buckets_above_one_page_are_counted_not_walked() -> None:
    xp = chosen(engine_plan(bound(PHASE)), {"s0": 3000})

    assert xp.runs[0].strategy == "count_fan_out"


def test_a_throttled_registry_walks_before_it_fans_out() -> None:
    run = chosen(engine_plan(bound(PHASE)), {"s0": 3000}, prefer_walk=True).runs[0]

    assert run.strategy == "walk"


def test_an_open_vocabulary_above_one_page_is_walked_with_a_presence_push_down() -> None:
    presence = essie.not_(essie.missing("LocationCountry"))
    country = replace(COUNTRY, presence=presence)

    run = chosen(engine_plan(bound(country)), {"s0": 3000}).runs[0]

    assert run.strategy == "walk" and run.walk_scope is not None
    assert run.walk_scope.extra == (presence,)
    assert run.scope.extra == ()


def test_an_open_vocabulary_of_any_size_is_walked_while_it_fits_the_time_left() -> None:
    # 80,000 trials are 80 pages and 80 counts: 32 s at 5 requests a second.
    plan = engine_plan(bound(COUNTRY))

    assert chosen(plan, {"s0": 80_000}, replace(LIMITS, walk_budget_s=40)).runs[0].strategy == "walk"
    assert chosen(plan, {"s0": 9_000_000}).runs[0].strategy == "walk"  # no deadline, no bound


def test_a_scope_that_cannot_be_read_in_the_time_left_is_too_broad_and_nothing_is_read_in_its_place() -> None:
    plan = engine_plan(bound(COUNTRY))

    answer = choose_strategy(plan, {"s0": 80_000}, replace(LIMITS, walk_budget_s=20), prefer_walk=False)

    assert isinstance(answer, Outcome) and (answer.kind, answer.reason) == ("clarification", "too_broad")
    assert "80,000 trials match" in answer.message and "32 s" in answer.message
    assert "20 s are left" in answer.message


def test_the_time_estimate_counts_every_compared_scope() -> None:
    scopes = (scope("A", scope_id="s0"), scope("B", scope_id="s1"))
    plan = engine_plan(bound(COUNTRY), scopes=scopes)
    limits = replace(LIMITS, walk_budget_s=20)

    assert chosen(plan, {"s0": 30_000, "s1": 1_000}, limits).runs[0].strategy == "walk"
    answer = choose_strategy(plan, {"s0": 30_000, "s1": 30_000}, limits, prefer_walk=False)
    assert isinstance(answer, Outcome) and answer.reason == "too_broad"
    assert "reading all 60,000 would take about 24 s" in answer.message


def test_a_drug_network_is_walked_in_full() -> None:
    drug = replace(DRUG, presence=essie.area("InterventionType", "DRUG"))
    plan = engine_plan(bound(drug, "node"), bound(drug, "node"), relation="network")

    run = chosen(plan, {"s0": 9000}).runs[0]

    assert run.strategy == "walk"
    assert run.walk_scope is not None and run.walk_scope.extra == (essie.area("InterventionType", "DRUG"),)


def test_scatter_points_are_read_from_every_trial() -> None:
    plan = engine_plan(rows=PointRows("enrollment", "site_count", None))

    assert chosen(plan, {"s0": 9000}).runs[0].strategy == "walk"
    assert chosen(plan, {"s0": 3000}).runs[0].strategy == "walk"


def test_a_date_axis_too_big_to_read_is_counted_and_its_window_is_shortened_to_fit_the_bill() -> None:
    window = Window("year", "1990", "2014")
    plan = engine_plan(bound(START_DATE), bound(PHASE, "series"), relation="series", window=window)

    xp = chosen(plan, {"s0": 9000}, replace(LIMITS, walk_budget_s=1))

    # Three phase buckets and three extra counts: the longest window with 3 * periods + 3 <= 60 is 19.
    assert xp.runs[0].strategy == "count_fan_out"
    assert xp.window == Window("year", "1996", "2014")
    # Not `window_clamped`: this cut is the time left on one request, so the answer must not be cached.
    assert [note.code for note in xp.warnings] == ["window_shortened_for_time"]


def test_a_date_axis_that_cannot_be_counted_even_for_five_periods_asks_for_a_narrower_question() -> None:
    plan = engine_plan(bound(START_DATE), window=Window("year", "1990", "2014"))

    answer = choose_strategy(plan, {"s0": 9000}, Limits(1000, 7, walk_budget_s=1), prefer_walk=False)

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


def test_every_series_states_its_own_size_in_the_reason_for_its_strategy() -> None:
    scopes = (scope("A", scope_id="s0"), scope("B", scope_id="s1"))

    xp = chosen(
        engine_plan(bound(COUNTRY), scopes=scopes),
        {"s0": 2971, "s1": 2029},
        replace(LIMITS, one_page_max=100),
    )

    assert [run.reason for run in xp.runs] == [
        "All 2,971 trials were read and grouped here.",
        "All 2,029 trials were read and grouped here.",
    ]


def test_a_fan_out_states_each_series_own_count_too() -> None:
    scopes = (scope("A", scope_id="s0"), scope("B", scope_id="s1"))

    xp = chosen(engine_plan(bound(PHASE), scopes=scopes), {"s0": 5000, "s1": 3000})

    assert [run.reason.split(" trials ")[0] for run in xp.runs] == ["5,000", "3,000"]
