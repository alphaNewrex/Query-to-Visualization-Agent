"""Lowering the canonical plan into the engine's plan."""

from dataclasses import dataclass, field

from ctviz.catalog.fields import Window
from ctviz.contract.plan import Aggregate, Analysis, Entity, Network, QueryPlan, Relate, TrialList
from ctviz.contract.request import QueryRequest, RequestOptions
from ctviz.engine.lower import EnginePlan, ListRows, PointRows, Resolved, lower_plan

from .engine_support import COUNTRY, DRUG, PHASE, PUBLIC_PLAN, SPONSOR, START_DATE, scope

CATALOG = {spec.key: spec for spec in (PHASE, START_DATE, COUNTRY, DRUG, SPONSOR)}
RESOLVED = Resolved(entities=(), scopes=(scope(),), matched={"s0": 1}, warnings=(), assumptions=())


@dataclass(frozen=True)
class Version:
    data_timestamp: str = "2026-10-06T09:00:05"


@dataclass(frozen=True)
class Planned:
    plan: QueryPlan
    request: QueryRequest | None = None
    options: RequestOptions = field(default_factory=lambda: RequestOptions(citations_per_datum=3))


def lower(analysis: Analysis, **filters: int | None) -> EnginePlan:
    plan = PUBLIC_PLAN.model_copy(
        update={
            "analysis": analysis,
            "filters": PUBLIC_PLAN.filters.model_copy(update=filters),
            "entities": [
                Entity(kind="drug", value="a", role="compare"),
                Entity(kind="drug", value="b", role="compare"),
            ],
        }
    )
    return lower_plan(Planned(plan), RESOLVED, CATALOG, Version())


def aggregate(
    dimension: str, series: str | None = None, unit: str | None = None, top_n: int | None = None
) -> Aggregate:
    return Aggregate(kind="aggregate", dimension=dimension, series=series, time_unit=unit, top_n=top_n)


def test_a_date_axis_defaults_to_the_latest_25_years_ending_where_the_data_ends() -> None:
    plan = lower(aggregate("start_date"))

    assert plan.window == Window("year", "2002", "2026")
    assert plan.dimensions[0].time_unit == "year" and plan.dimensions[0].role == "axis"


def test_stated_years_make_the_window_and_a_quarter_axis_counts_quarters() -> None:
    assert lower(aggregate("start_date"), year_from=2015).window == Window("year", "2015", "2026")
    assert lower(aggregate("start_date"), year_from=2015, year_to=2018).window == Window(
        "year", "2015", "2018"
    )
    quarters = lower(aggregate("start_date", unit="quarter"), year_from=2024).window
    assert quarters == Window("quarter", "2024-Q1", "2026-Q4")
    months = lower(aggregate("start_date", unit="month")).window
    assert months is not None and (months.first, months.last) == ("2024-10", "2026-10")


def test_a_category_has_no_window_and_a_series_makes_a_two_dimension_relation() -> None:
    plan = lower(aggregate("country", "phase", top_n=500))

    assert plan.window is None
    assert [(d.spec.key, d.role) for d in plan.dimensions] == [("country", "axis"), ("phase", "series")]
    assert (plan.relation, plan.top_n, plan.compare_kind, plan.citations_per_datum) == (
        "series",
        50,
        "drug",
        3,
    )


def test_a_network_of_one_kind_of_drug_pairs_by_arm_unless_the_plan_says_otherwise() -> None:
    drugs = lower(Network(kind="network", source="drug", target="drug", link=None))
    by_trial = lower(Network(kind="network", source="drug", target="drug", link="same_trial"))
    mixed = lower(Network(kind="network", source="sponsor", target="drug", link=None))

    assert (drugs.relation, drugs.pairing) == ("network", "same_arm")
    assert by_trial.pairing == "same_trial" and mixed.pairing == "same_trial"
    assert [d.role for d in mixed.dimensions] == ["node", "node"]
    assert (drugs.min_link_weight, drugs.max_links, drugs.max_series) == (2, 60, 10)


def test_rows_plans_have_no_dimensions() -> None:
    points = lower(Relate(kind="relate", x="enrollment", y="site_count", color_by="phase"))
    listing = lower(TrialList(kind="trial_list", sort_by="start_date", order="asc", limit=None))
    long_listing = lower(TrialList(kind="trial_list", sort_by="start_date", order="asc", limit=400))

    assert points.dimensions == () and isinstance(points.rows, PointRows) and points.rows.color is not None
    assert listing.rows == ListRows("start_date", "asc") and listing.top_n == 10
    assert long_listing.top_n == 50
