"""Numeric measures: the median, mean or sum of a field per cell, read by a walk and drawn with its unit."""

import dataclasses

from ctviz.catalog.fields import Window
from ctviz.engine.aggregate import aggregate
from ctviz.engine.lower import MeasureSpec
from ctviz.engine.shape import shape
from ctviz.viz import measure as measures
from tests.unit.engine_support import PHASE, bound, engine_plan, scope, study
from tests.unit.plan_samples import aggregate as aggregate_of
from tests.unit.plan_samples import plan, request
from tests.unit.test_check_plan import check
from tests.unit.test_engine_shape import result_of
from tests.unit.test_engine_strategy import chosen
from tests.unit.test_viz_build import (
    PHASE as PHASE_BARS,
)
from tests.unit.test_viz_build import (
    START_YEAR,
    ShapedResult,
    cell,
    frame,
    plan_of,
    respond,
    rows,
    trial,
)

DURATION = MeasureSpec("median", "duration_months")


def test_a_statistic_is_always_read_by_a_walk_even_where_a_count_would_fan_out() -> None:
    xp = chosen(engine_plan(bound(PHASE), measure=DURATION), {"s0": 3000})

    assert xp.runs[0].strategy == "walk"
    assert "StartDate" in xp.runs[0].fields and "CompletionDate" in xp.runs[0].fields


def test_a_single_statistic_is_a_walk_and_not_a_sample_call() -> None:
    xp = chosen(engine_plan(measure=MeasureSpec("sum", "enrollment")), {"s0": 3000})

    assert xp.runs[0].strategy == "walk"


def test_a_statistic_of_a_large_scope_reads_every_trial() -> None:
    xp = chosen(engine_plan(bound(PHASE), measure=DURATION), {"s0": 80_000})

    assert xp.runs[0].strategy == "walk"


def test_the_median_is_taken_over_the_trials_that_have_a_value_and_the_rest_is_counted() -> None:
    plan_ = engine_plan(bound(PHASE), measure=DURATION)
    trials = [
        study(1, phases=("PHASE3",), start="2020-01", completion="2021-01"),  # 12 months
        study(2, phases=("PHASE3",), start="2020-01", completion="2022-01"),  # 24
        study(3, phases=("PHASE3",), start="2020-01", completion="2020-04"),  # 3
        study(4, phases=("PHASE3",), start="2020-01"),  # no completion date
        study(5, phases=("PHASE3",), start="2021-01", completion="2020-01"),  # ends before it starts
    ]

    frame_ = aggregate(trials, plan_, scope(), {}, 2)

    assert frame_.analyzed == 3 and frame_.cells[("PHASE3",)].values == [12.0, 24.0, 3.0]
    assert frame_.excluded["no_duration_months"].count == 2
    assert measures.value_of(frame_.cells[("PHASE3",)].values, DURATION) == 12
    assert measures.value_of([12.0, 24.0, 3.0, 5.0], DURATION) == 8.5
    assert measures.value_of([1.0, 2.0, 4.0], MeasureSpec("mean", "site_count")) == 2.3
    assert measures.value_of([1.0, 2.0, 4.0], MeasureSpec("sum", "enrollment")) == 7


def test_a_trial_that_lists_no_site_has_no_site_count_to_average() -> None:
    plan_ = engine_plan(measure=MeasureSpec("mean", "site_count"))
    trials = [study(1, countries=["A", "B", "C"]), study(2, countries=["A"]), study(3)]

    frame_ = aggregate(trials, plan_, scope(), {}, 2)

    assert frame_.cells[()].values == [3.0, 1.0] and frame_.excluded["no_site_count"].count == 1
    cited = frame_.cells[()].sample[0].evidence
    assert cited[0].path.endswith("locations[0].country")


def test_the_values_of_the_categories_that_a_top_n_merges_are_pooled() -> None:
    plan_ = engine_plan(bound(PHASE), top_n=2, measure=MeasureSpec("sum", "enrollment"))
    trials = [study(n, phases=(f"PHASE{n}",), enrollment=10 * n) for n in (1, 2, 3, 4)]

    shaped = shape(result_of(plan_, trials), plan_)

    assert sum(cell_.trials for cell_ in shaped.frames[0].cells) == 4


def test_a_bar_chart_of_a_statistic_names_the_measure_and_the_unit_everywhere() -> None:
    measure = MeasureSpec("median", "duration_months")
    cells = [
        dataclasses.replace(cell(("PHASE2",), 2, trial(1)), values=[10.0, 20.0]),
        dataclasses.replace(cell(("PHASE3",), 1, trial(2)), values=[40.0]),
    ]
    plan_ = plan_of(dimensions=(PHASE_BARS,), measure=measure)

    response = respond(plan_, ShapedResult(frames=(frame("pembrolizumab", (PHASE_BARS,), cells),)))

    viz = response.visualization
    assert viz.type == "bar_chart"
    assert viz.encoding.y.field == "median_duration_months" and viz.encoding.y.unit == "months"
    assert viz.encoding.y.title == "Median duration (months)" and viz.encoding.y.format == ".1f"
    assert viz.title == "Median duration (months) by phase: pembrolizumab"
    assert [(r["phase"], r["median_duration_months"], r["trial_count"]) for r in rows(response)] == [
        ("PHASE2", 15.0, 2),
        ("PHASE3", 40.0, 1),
    ]
    assert response.message == "PHASE3 has the highest median duration (months): 40.0 (1 trials measured)."
    assert response.meta.interpretation is not None
    interpretation = response.meta.interpretation
    assert interpretation.measure is not None
    assert (interpretation.measure.aggregate, interpretation.measure.of, interpretation.measure.unit) == (
        "median",
        "duration_months",
        "months",
    )
    assert any("computed here" in note for note in response.meta.assumptions)


def test_compared_scopes_with_a_statistic_are_bars_with_one_value_each() -> None:
    measure = MeasureSpec("mean", "site_count")
    a = frame("pembrolizumab", (), [dataclasses.replace(cell((), 2, trial(1)), values=[2.0, 4.0])])
    b = frame("nivolumab", (), [dataclasses.replace(cell((), 1, trial(2)), values=[10.0])])
    plan_ = plan_of(
        scopes=(a.scope, dataclasses.replace(b.scope, id="s1")), compare_kind="drug", measure=measure
    )

    response = respond(plan_, ShapedResult(frames=(a, b)))

    assert response.visualization.type == "bar_chart"
    assert [(r["group"], r["mean_site_count"]) for r in rows(response)] == [
        ("pembrolizumab", 3.0),
        ("nivolumab", 10.0),
    ]
    assert response.message.startswith("nivolumab has the highest mean number of sites per trial: 10.0")


def test_a_single_statistic_is_a_metric_and_a_time_axis_leaves_an_empty_period_without_a_value() -> None:
    measure = MeasureSpec("sum", "enrollment")
    one = frame("x", (), [dataclasses.replace(cell((), 3, trial(1)), values=[100.0, 200.0, 300.0])])
    metric = respond(plan_of(measure=measure), ShapedResult(frames=(one,)))

    assert metric.visualization.type == "metric" and rows(metric)[0]["sum_enrollment"] == 600
    assert metric.message == "Total enrollment (participants) for x: 600, over 3 trials."

    cells = [dataclasses.replace(cell(("2015",), 1, trial(1)), values=[7.0])]
    plan_ = plan_of(
        dimensions=(START_YEAR,),
        window=Window("year", "2015", "2016"),
        measure=MeasureSpec("median", "enrollment"),
    )
    series = respond(plan_, ShapedResult(frames=(frame("x", (START_YEAR,), cells),)))
    assert [(r["start_year"], r["median_enrollment"]) for r in rows(series)] == [
        ("2015", 7.0),
        ("2016", None),
    ]


def test_a_statistic_needs_both_halves_and_a_sum_of_durations_is_refused() -> None:
    half = plan(analysis={**aggregate_of("phase"), "statistic": "median"})
    silly = plan(analysis={**aggregate_of("phase"), "statistic": "sum", "of": "duration_months"})
    good = plan(analysis={**aggregate_of("phase"), "statistic": "mean", "of": "enrollment"})

    assert [i.code for i in check(half, request()).blocking] == ["measure_incomplete"]
    assert [i.code for i in check(silly, request()).blocking] == ["measure_not_sensible"]
    assert check(good, request()).blocking == ()


def test_a_chart_preference_of_a_histogram_does_not_fit_a_statistic() -> None:
    model = plan(
        analysis={**aggregate_of("enrollment"), "statistic": "median", "of": "duration_months"},
        chart_preference="histogram",
    )

    result = check(model, request())

    assert result.plan.chart_preference is None
