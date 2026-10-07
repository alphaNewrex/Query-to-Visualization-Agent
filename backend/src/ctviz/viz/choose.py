"""Which visualization type answers the plan: the rule table of section 4.12, as a pure function."""

from dataclasses import dataclass, replace
from typing import Literal

from ctviz.contract.plan import ChartType
from ctviz.contract.response import Note
from ctviz.engine.lower import EnginePlan, ListRows, PointRows
from ctviz.viz import text
from ctviz.viz.shaped import ShapedResult

# What "aggregated" means for a table preference: results that are rows of counts.
_AGGREGATED: frozenset[ChartType] = frozenset({"bar_chart", "time_series", "histogram", "metric"})


@dataclass(frozen=True)
class ChartOptions:
    """The drawing choices that belong to a type."""

    mark: Literal["line", "bar"] = "line"
    orientation: Literal["vertical", "horizontal"] = "vertical"
    stack: Literal["none", "stacked"] = "none"
    layout: Literal["force", "bipartite"] = "bipartite"
    log_x: bool = False
    log_y: bool = False
    over_bins: bool = False  # a bar chart over the bin labels of a histogram


@dataclass(frozen=True)
class ChartChoice:
    """The type, its options and the rule that fired (`meta.interpretation.chart_rationale`)."""

    type: ChartType
    rationale: str
    options: ChartOptions = ChartOptions()
    table_of: ChartType | None = None  # a table preference: the aggregate whose rows the table shows
    warnings: tuple[Note, ...] = ()


def choose_chart(plan: EnginePlan, shaped: ShapedResult) -> ChartChoice:
    """The type that fits the plan's shape, adjusted by the chart preference where the data allows."""
    return _with_preference(_by_shape(plan, shaped), plan.chart_preference)


def _by_shape(plan: EnginePlan, shaped: ShapedResult) -> ChartChoice:
    if isinstance(plan.rows, PointRows):
        return _scatter(plan.rows, shaped)
    if isinstance(plan.rows, ListRows):
        return ChartChoice("table", text.CHART_RATIONALE[2])
    if plan.relation == "network":
        same_kind = len({dim.spec.key for dim in plan.dimensions}) == 1
        rule = 13 if same_kind else 12
        return ChartChoice(
            "network_graph",
            text.CHART_RATIONALE[rule],
            ChartOptions(layout="force" if same_kind else "bipartite"),
        )
    several_scopes = len(plan.scopes) > 1
    if not plan.dimensions:
        return (
            ChartChoice("bar_chart", text.CHART_RATIONALE[4])
            if several_scopes
            else ChartChoice("metric", text.CHART_RATIONALE[3])
        )
    axis = plan.dimensions[0]
    series = plan.dimensions[1] if len(plan.dimensions) > 1 else None
    if axis.spec.kind == "date":
        return ChartChoice("time_series", text.CHART_RATIONALE[5], ChartOptions(mark="line"))
    if axis.spec.kind == "number":
        if not several_scopes and series is None and plan.measure is None:
            return ChartChoice("histogram", text.CHART_RATIONALE[6])
        return ChartChoice("bar_chart", text.CHART_RATIONALE[7], ChartOptions(over_bins=True))
    return _bars(axis.spec.is_ordinal, several_scopes, series.spec.is_exclusive if series else None)


def _bars(is_ordinal: bool, several_scopes: bool, series_is_exclusive: bool | None) -> ChartChoice:
    rationale = text.CHART_RATIONALE[8 if is_ordinal else 9]
    orientation: Literal["vertical", "horizontal"] = "vertical" if is_ordinal else "horizontal"
    stack: Literal["none", "stacked"] = "none"
    if several_scopes or series_is_exclusive is False:
        rationale += " " + text.CHART_RATIONALE[10]
    elif series_is_exclusive:
        stack = "stacked"
        rationale += " " + text.CHART_RATIONALE[11]
    return ChartChoice("bar_chart", rationale, ChartOptions(orientation=orientation, stack=stack))


def _scatter(rows: PointRows, shaped: ShapedResult) -> ChartChoice:
    def is_log(field: str) -> bool:
        values = [row.values.get(field) for row in shaped.trials]
        return (
            field == "enrollment"
            and bool(values)
            and all(isinstance(v, int | float) and not isinstance(v, bool) and v > 0 for v in values)
        )

    return ChartChoice(
        "scatter_plot", text.CHART_RATIONALE[1], ChartOptions(log_x=is_log(rows.x), log_y=is_log(rows.y))
    )


def _with_preference(base: ChartChoice, preference: ChartType | None) -> ChartChoice:
    """Honour a preference inside what the data shape allows; otherwise keep the choice and say so."""
    if preference is None or preference == base.type:
        return base
    if preference == "bar_chart" and base.type == "time_series":
        return replace(
            base, rationale=base.rationale + text.PREFERENCE_TIME_AS_BARS, options=ChartOptions(mark="bar")
        )
    if preference == "bar_chart" and base.type == "histogram":
        return replace(
            base,
            type="bar_chart",
            rationale=base.rationale + text.PREFERENCE_HISTOGRAM_AS_BARS,
            options=ChartOptions(over_bins=True),
        )
    if preference == "table" and base.type in _AGGREGATED:
        return replace(
            base, type="table", table_of=base.type, rationale=base.rationale + text.PREFERENCE_AS_TABLE
        )
    return replace(base, warnings=(text.chart_preference_ignored(preference, base.type),))
