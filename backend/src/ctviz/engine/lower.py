"""The internal plan: what the engine runs, built from the canonical `QueryPlan` and the entity resolutions.

Nothing after the resolve stage looks at the model-facing plan or at the request. Only the types are
declared here. `BoundTerm`, `DateRange` and `Scope` belong to `ctviz.ctgov.params`, where the request
parameters are written, and `BoundDimension` and `Window` to the catalogue; they are re-exported so
that one import covers the whole internal plan.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal, Protocol

from ctviz.catalog.fields import BoundDimension, FieldSpec, Window
from ctviz.contract.plan import (
    Aggregate,
    ChartType,
    EntityKind,
    Network,
    NumericField,
    Pairing,
    PlanFilters,
    QueryPlan,
    Relate,
    SortField,
    SortOrder,
    Statistic,
    TimeUnit,
    Total,
    TrialList,
)
from ctviz.contract.request import QueryRequest, RequestOptions
from ctviz.contract.response import Adjustment, EntityResolution, Note
from ctviz.ctgov.params import BoundTerm, DateRange, Scope
from ctviz.engine import windows

__all__ = [
    "DEFAULT_WINDOW_PERIODS",
    "BoundDimension",
    "BoundTerm",
    "DateRange",
    "EnginePlan",
    "ListRows",
    "MeasureSpec",
    "PointRows",
    "Resolved",
    "RowSpec",
    "Scope",
    "Window",
    "axis_years",
    "lower_plan",
]

DEFAULT_TOP_N: Final = 15
MAX_TOP_N: Final = 50
DEFAULT_LIST_ROWS: Final = 10
MAX_LIST_ROWS: Final = 50
MAX_SERIES: Final = 10
MIN_LINK_WEIGHT: Final = 2
MAX_LINKS: Final = 60
DEFAULT_WINDOW_PERIODS: Final = 25


@dataclass(frozen=True)
class PointRows:
    """`relate`: one point per trial with two numeric fields, optionally coloured by a closed dimension."""

    x: NumericField
    y: NumericField
    color: BoundDimension | None


@dataclass(frozen=True)
class ListRows:
    """`trial_list`: one sorted page of trials. How many rows is `EnginePlan.top_n`."""

    sort_by: SortField
    order: SortOrder


RowSpec = PointRows | ListRows


@dataclass(frozen=True)
class MeasureSpec:
    """`aggregate` and `total` with a statistic: the median, mean or sum of a numeric field per cell."""

    statistic: Statistic
    field: NumericField


@dataclass(frozen=True)
class EnginePlan:
    """Everything the stages after resolution need to know, with every default already applied."""

    scopes: tuple[Scope, ...]  # 1 to 4; more than one means a comparison
    compare_kind: EntityKind | None
    dimensions: tuple[BoundDimension, ...]  # 0 to 2
    relation: Literal["series", "network"] | None
    pairing: Pairing
    rows: RowSpec | None  # set for relate and trial_list: per-trial rows, no grouping
    window: Window | None  # concrete periods for a date axis
    top_n: int
    max_series: int
    min_link_weight: int
    max_links: int
    chart_preference: ChartType | None
    citations_per_datum: int
    public: QueryPlan  # the canonical plan echoed in meta.plan
    measure: MeasureSpec | None = None  # None counts trials

    @property
    def axis_years(self) -> tuple[int | None, int | None]:
        """The years the plan states for its time axis: the year range, when it is a range of the axis' field.

        A range on another date field limits the trials and says nothing about the periods shown: of the
        trials that started in 2020, the years they completed in are not limited to 2020.
        """
        return axis_years(self.public.filters, self.dimensions[0].spec.key if self.dimensions else None)


@dataclass(frozen=True)
class Resolved:
    """What the registry made of the entities: the scopes to run and how many trials each matches."""

    entities: tuple[EntityResolution, ...]
    scopes: tuple[Scope, ...]
    matched: Mapping[str, int]  # trials matched by one count probe, by scope id
    warnings: tuple[Note, ...]
    assumptions: tuple[str, ...]
    # The plan with each entity read as the kind the registry's counts decided, and what changed.
    plan: QueryPlan | None = None
    adjustments: tuple[Adjustment, ...] = ()


class PlannedLike(Protocol):
    """What the engine reads of the planning stage's result (`PlannedQuery`)."""

    @property
    def plan(self) -> QueryPlan: ...

    @property
    def request(self) -> QueryRequest | None: ...

    @property
    def options(self) -> RequestOptions: ...


class DataVersion(Protocol):
    """The registry's data version; the window of a date axis ends where the data ends."""

    @property
    def data_timestamp(self) -> str: ...


def lower_plan(
    planned: PlannedLike, resolved: Resolved, catalog: Mapping[str, FieldSpec], version: DataVersion
) -> EnginePlan:
    """The internal plan of a canonical plan whose entities have been resolved into scopes."""
    plan = planned.plan
    analysis = plan.analysis
    dimensions: tuple[BoundDimension, ...] = ()
    relation: Literal["series", "network"] | None = None
    pairing: Pairing = "same_trial"
    rows: RowSpec | None = None
    top_n = DEFAULT_TOP_N
    measure: MeasureSpec | None = None
    if isinstance(analysis, Aggregate | Total) and analysis.statistic is not None and analysis.of is not None:
        measure = MeasureSpec(analysis.statistic, analysis.of)
    match analysis:
        case Aggregate():
            dimensions = (_bind(catalog, analysis.dimension, analysis.time_unit, "axis"),)
            if analysis.series is not None:
                dimensions += (_bind(catalog, analysis.series, None, "series"),)
                relation = "series"
            top_n = min(analysis.top_n or DEFAULT_TOP_N, MAX_TOP_N)
        case Total():
            pass
        case Network():
            dimensions = (
                _bind(catalog, analysis.source, None, "node"),
                _bind(catalog, analysis.target, None, "node"),
            )
            relation = "network"
            pairing = analysis.link or (
                "same_arm" if analysis.source == analysis.target == "drug" else "same_trial"
            )
        case Relate():
            color = None if analysis.color_by is None else _bind(catalog, analysis.color_by, None, "series")
            rows = PointRows(x=analysis.x, y=analysis.y, color=color)
        case TrialList():
            rows = ListRows(sort_by=analysis.sort_by, order=analysis.order)
            top_n = min(analysis.limit or DEFAULT_LIST_ROWS, MAX_LIST_ROWS)
        case _:
            raise ValueError(f"A {analysis.kind} plan has nothing to execute.")

    axis = dimensions[0] if dimensions else None
    window = None
    if axis is not None and axis.time_unit is not None:
        window = _window(axis_years(plan.filters, axis.spec.key), axis.time_unit, version.data_timestamp)
    return EnginePlan(
        scopes=resolved.scopes,
        compare_kind=next((entity.kind for entity in plan.entities if entity.role == "compare"), None),
        dimensions=dimensions,
        relation=relation,
        pairing=pairing,
        rows=rows,
        window=window,
        top_n=top_n,
        max_series=MAX_SERIES,
        min_link_weight=MIN_LINK_WEIGHT,
        max_links=MAX_LINKS,
        chart_preference=plan.chart_preference,
        citations_per_datum=planned.options.citations_per_datum,
        public=plan,
        measure=measure,
    )


def _bind(
    catalog: Mapping[str, FieldSpec],
    key: str,
    time_unit: TimeUnit | None,
    role: Literal["axis", "series", "node"],
) -> BoundDimension:
    spec = catalog[key]
    return BoundDimension(
        spec=spec, time_unit=(time_unit or "year") if spec.kind == "date" else None, role=role
    )


def axis_years(filters: PlanFilters, axis_key: str | None) -> tuple[int | None, int | None]:
    """The years of a plan's range that belong to the date axis `axis_key`; none for a range on another field.

    A plan that names no date field has its range on the axis' field, as the scope's date range does.
    """
    if filters.date_field is not None and filters.date_field != axis_key:
        return None, None
    return filters.year_from, filters.year_to


def _window(years: tuple[int | None, int | None], unit: TimeUnit, data_timestamp: str) -> Window:
    """The periods of a date axis: the stated years, else the latest 25 ending where the data ends."""
    year_from, year_to = years
    if year_to is None:
        last = windows.label_of_day(unit, data_timestamp[:10])
    else:
        last = windows.of_year(unit, year_to)[1]
    if year_from is None:
        return windows.ending_at(unit, last, DEFAULT_WINDOW_PERIODS)
    first = windows.of_year(unit, year_from)[0]
    return Window(unit, first, max(first, last))
