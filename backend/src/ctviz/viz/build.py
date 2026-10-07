"""One builder per visualization type: a shaped result becomes render-ready channels and rows.

Rows follow sections 5.1 to 5.3: flat, one per mark, with explicit domains, row field names that the
encoding names, and counts that equal the citation count. The grid of an axis and a series is rebuilt
here from the plan (periods from the window, bins from the shaper), so a missing cell is a zero and not
a hole in the grid.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Literal

from ctviz.catalog import vocab
from ctviz.catalog.countries import load_country_table
from ctviz.catalog.fields import BoundDimension, Window
from ctviz.catalog.periods import period_of, window_labels
from ctviz.contract.response import (
    BarChart,
    BarChartEncoding,
    CategoryChannel,
    ChannelSort,
    Datum,
    Edge,
    FieldDef,
    FieldRef,
    Histogram,
    HistogramEncoding,
    Metric,
    MetricEncoding,
    NetworkData,
    NetworkEdgeEncoding,
    NetworkEncoding,
    NetworkGraph,
    NetworkNodeEncoding,
    Node,
    QuantitativeChannel,
    Scalar,
    ScatterPlot,
    ScatterPlotEncoding,
    Table,
    TableEncoding,
    TemporalChannel,
    TimeSeries,
    TimeSeriesEncoding,
    Visualization,
)
from ctviz.ctgov.client import ApiVersion
from ctviz.ctgov.params import Scope
from ctviz.engine.frame import Cell, Frame
from ctviz.engine.lower import EnginePlan, ListRows, PointRows
from ctviz.viz import measure as measures
from ctviz.viz import text
from ctviz.viz.choose import ChartChoice
from ctviz.viz.citations import STUDY_URL, CitationBook, Cited
from ctviz.viz.shaped import ShapedResult

COUNT_FIELD = "trial_count"
GROUP_FIELD = "group"
SPLIT_SERIES_FIELD = "group_split"
SHARE_FIELD = "share"
SHOWN_SHARE_FIELD = "share_of_shown"  # a state's count over the sum of the counts of the bars drawn
_ColumnType = Literal["nominal", "temporal", "quantitative"]
_TABLE_COLUMNS = ("phase", "overall_status", "start_date", "enrollment", "lead_sponsor")


@dataclass(frozen=True)
class Built:
    """A drawn visualization with the sentence that headlines it."""

    visualization: Visualization
    message: str
    data_points: int  # rows, or nodes plus links


@dataclass(frozen=True)
class _Inputs:
    plan: EnginePlan
    shaped: ShapedResult
    choice: ChartChoice
    book: CitationBook
    version: ApiVersion

    @property
    def labels(self) -> list[str | None]:
        return [text.scope_name(frame.scope) for frame in self.shaped.frames]

    @property
    def is_comparison(self) -> bool:
        return len(self.shaped.frames) > 1

    @property
    def filters(self) -> list[str]:
        return scope_filters(self.plan, self.shaped.frames[0].scope)


def build_visualization(
    plan: EnginePlan, shaped: ShapedResult, choice: ChartChoice, book: CitationBook, version: ApiVersion
) -> Built:
    """Build the chosen type; a table preference over an aggregate shows the rows of that aggregate."""
    inputs = _Inputs(plan, shaped, choice, book, version)
    if choice.type == "table" and choice.table_of is None:
        return _trial_table(inputs)
    built = _BUILDERS[choice.table_of or choice.type](inputs)
    return _aggregate_as_table(built) if choice.type == "table" else built


# --- Names, channels and cells ----------------------------------------------------------------------------


def field_name(dim: BoundDimension) -> str:
    """The row key of a dimension (section 5.3): the catalogue key, a date stem and unit, or `_bin`."""
    spec = dim.spec
    if spec.kind == "date":
        return f"{spec.key.removesuffix('_date')}_{dim.time_unit or 'year'}"
    return f"{spec.key}_bin" if spec.kind == "number" else spec.key


def _cited_fields(cited: Cited) -> dict[str, object]:
    return {
        "citations": cited.citations,
        "citation_count": cited.citation_count,
        "source_url": cited.source_url,
    }


def _datum(fields: Mapping[str, Scalar], cited: Cited) -> Datum:
    return Datum.model_validate({**fields, **_cited_fields(cited)})


def scope_filters(plan: EnginePlan, scope: Scope) -> list[str]:
    """What limits a scope beyond its entities; the scopes of one question share their filters."""
    axis = plan.dimensions[0] if plan.dimensions else None
    skip = axis.spec.pieces[0] if axis is not None and axis.spec.kind == "date" else None
    return text.scope_filters(scope, skip_date_piece=skip)


def is_partial(window: Window | None, version: ApiVersion) -> bool:
    """Whether the last period of an axis is the one the data date falls in, so it is still running."""
    if window is None:
        return False
    return period_of(version.data_timestamp[:10], window.unit) == window.last


def _y_channel(i: _Inputs, count_title: str = "Trials") -> QuantitativeChannel:
    """The drawn number: a count of trials, or the statistic of the plan."""
    measure = i.plan.measure
    return measures.channel(measure) if measure is not None else _count_channel(count_title)


def _measured_tooltip(i: _Inputs) -> list[FieldDef]:
    """What a statistic is the statistic of: the number of trials that had a value."""
    return [
        FieldDef(
            field=COUNT_FIELD,
            title="Trials measured",
            type="quantitative",
            unit="trials",
            format=",d",
            href_field=None,
        )
    ]


def _count_channel(title: str = "Trials") -> QuantitativeChannel:
    return QuantitativeChannel(
        field=COUNT_FIELD, type="quantitative", title=title, unit="trials", format=",d", scale="linear"
    )


def _sort(dim: BoundDimension) -> ChannelSort:
    if dim.spec.is_ordinal:
        return ChannelSort(by="natural", order="ascending")
    return ChannelSort(by="value", order="descending")


def _category_channel(dim: BoundDimension, domain: list[str]) -> CategoryChannel:
    return CategoryChannel(
        field=field_name(dim),
        type="ordinal" if dim.spec.is_ordinal else "nominal",
        title=dim.spec.title,
        domain=domain,
        sort=_sort(dim),
        is_exclusive=dim.spec.is_exclusive,
    )


def _total_cell(frame: Frame) -> Cell:
    return frame.cells.get((), Cell(key=(), trials=frame.analyzed))


def _label(cell: Cell, position: int) -> str:
    return cell.labels[position] if len(cell.labels) > position else cell.key[position]


def _periods(window: Window) -> list[str]:
    """Every period label from the first to the last, so a row exists for an empty period too."""
    return window_labels(window)


# --- The grid of an axis and a series ---------------------------------------------------------------------


@dataclass(frozen=True)
class _Mark:
    x: str
    series: str | None
    cell: Cell
    analyzed: int  # the trials analysed in this mark's series: the denominator of its share
    group: str | None = None  # the compared value, when a comparison is also split
    split: str | None = None  # the value of the split, when a comparison is also split


@dataclass(frozen=True)
class _Grid:
    x_labels: list[str]
    series_labels: list[str]  # empty without a series
    marks: list[_Mark]
    is_split_comparison: bool = False  # series are (compared value, split value) pairs


def _drawn_periods(i: _Inputs, window: Window) -> list[str]:
    """The periods of the window, less the empty ones before the first trial unless a start year was named."""
    labels = _periods(window)
    if i.plan.public.filters.year_from is not None:
        return labels
    populated = {cell.key[0] for frame in i.shaped.frames for cell in frame.cells.values() if cell.trials}
    return labels[next((n for n, label in enumerate(labels) if label in populated), 0) :]


def _axis_entries(i: _Inputs, axis: BoundDimension) -> list[tuple[str, str]]:
    """(key, label) of every x value in display order."""
    if axis.spec.kind == "date" and i.plan.window is not None:
        return [(period, period) for period in _drawn_periods(i, i.plan.window)]
    if axis.spec.kind == "number":
        return [(b.label, b.label) for b in vocab.ENROLLMENT_BINS]
    seen: dict[str, str] = {}
    for frame in i.shaped.frames:
        for cell in frame.cells.values():
            seen.setdefault(cell.key[0], _label(cell, 0))
    entries = list(seen.items())
    return sorted(entries) if axis.spec.kind == "date" else entries


def _grid(i: _Inputs) -> _Grid:
    axis = i.plan.dimensions[0]
    x_entries = _axis_entries(i, axis)
    frames = i.shaped.frames
    if i.is_comparison:
        names = [frame.scope.label or frame.scope.id for frame in frames]
        if len(i.plan.dimensions) > 1:
            return _split_comparison_grid(i, x_entries, names)
        marks = [
            _Mark(label, name, _cell_or_empty(frame, (key,), (label,)), frame.analyzed)
            for key, label in x_entries
            for frame, name in zip(frames, names, strict=True)
        ]
        return _Grid([label for _, label in x_entries], names, marks)
    frame = frames[0]
    if len(i.plan.dimensions) == 1:
        marks = [
            _Mark(label, None, _cell_or_empty(frame, (key,), (label,)), frame.analyzed)
            for key, label in x_entries
        ]
        return _Grid([label for _, label in x_entries], [], marks)
    series_entries: dict[str, str] = {}
    for cell in frame.cells.values():
        series_entries.setdefault(cell.key[1], _label(cell, 1))
    marks = [
        _Mark(x_label, s_label, _cell_or_empty(frame, (x_key, s_key), (x_label, s_label)), frame.analyzed)
        for x_key, x_label in x_entries
        for s_key, s_label in series_entries.items()
    ]
    return _Grid([label for _, label in x_entries], list(series_entries.values()), marks)


def _split_comparison_grid(i: _Inputs, x_entries: list[tuple[str, str]], names: list[str]) -> _Grid:
    """A comparison that is also split: a series for each (compared value, split value) with trials."""
    frames = i.shaped.frames
    splits: dict[str, str] = {}
    for frame in frames:
        for cell in frame.cells.values():
            splits.setdefault(cell.key[1], _label(cell, 1))
    pairs = [
        (frame, name, s_key, s_label)
        for frame, name in zip(frames, names, strict=True)
        for s_key, s_label in splits.items()
        if any(cell.trials for key, cell in frame.cells.items() if key[1] == s_key)
    ]
    marks = [
        _Mark(
            x_label,
            f"{name} · {s_label}",
            _cell_or_empty(frame, (x_key, s_key), (x_label, s_label)),
            frame.analyzed,
            group=name,
            split=s_label,
        )
        for x_key, x_label in x_entries
        for frame, name, s_key, s_label in pairs
    ]
    return _Grid(
        [label for _, label in x_entries],
        [f"{name} · {s_label}" for _, name, _, s_label in pairs],
        marks,
        is_split_comparison=True,
    )


def _cell_or_empty(frame: Frame, key: tuple[str, ...], labels: tuple[str, ...]) -> Cell:
    return frame.cells.get(key, Cell(key=key, labels=labels))


def _series_channel(i: _Inputs, grid: _Grid) -> CategoryChannel | None:
    if not grid.series_labels:
        return None
    if grid.is_split_comparison:
        return CategoryChannel(
            field=SPLIT_SERIES_FIELD,
            type="nominal",
            title=text.split_series_title(i.plan.compare_kind, i.plan.dimensions[1].spec.title),
            domain=grid.series_labels,
            sort=None,
            is_exclusive=False,
        )
    if i.is_comparison:
        title = text.KIND_TITLES.get(i.plan.compare_kind or "", "Group")
        return CategoryChannel(
            field=GROUP_FIELD,
            type="nominal",
            title=title,
            domain=grid.series_labels,
            sort=None,
            is_exclusive=False,
        )
    return _category_channel(i.plan.dimensions[1], grid.series_labels)


def _split_title(i: _Inputs, grid: _Grid) -> str | None:
    """The title of the split of a split comparison, for the chart title; None otherwise."""
    return i.plan.dimensions[1].spec.title if grid.is_split_comparison else None


def _grid_row(
    i: _Inputs,
    mark: _Mark,
    x_field: str,
    series: CategoryChannel | None,
    with_share: bool,
    shown_total: int | None = None,
) -> Datum:
    fields: dict[str, Scalar] = {x_field: mark.x}
    if series is not None:
        fields[series.field] = mark.series
    if mark.group is not None:  # the two parts of a split comparison's series, as keys of their own
        fields[GROUP_FIELD] = mark.group
        fields[i.plan.dimensions[1].spec.key] = mark.split
    fields[COUNT_FIELD] = mark.cell.trials
    if i.plan.measure is not None:
        fields[measures.row_key(i.plan.measure)] = measures.value_of(mark.cell.values, i.plan.measure)
    elif with_share:
        fields[SHARE_FIELD] = round(mark.cell.trials / mark.analyzed, 4) if mark.analyzed else 0.0
    if shown_total is not None:
        fields[SHOWN_SHARE_FIELD] = round(mark.cell.trials / shown_total, 4) if shown_total else 0.0
    if i.plan.dimensions[0].spec.key == "country":
        fields["iso_alpha3"] = load_country_table().iso_alpha3(mark.x)  # so a map can be added later
    return _datum(fields, i.book.for_cell(mark.cell))


def _trials_phrase(i: _Inputs) -> str:
    parts = [(frame.scope.label if i.is_comparison else None, frame.analyzed) for frame in i.shaped.frames]
    return text.trials_phrase(parts, "trials measured" if i.plan.measure is not None else "trials")


def _subset_phrase(i: _Inputs) -> str | None:
    subset = next((f.subset for f in i.shaped.frames if f.subset is not None), None)
    if subset is None:
        return None
    return text.subset_phrase(
        subset.size, subset.first_posted_from.isoformat(), subset.first_posted_to.isoformat()
    )


def _subtitle(i: _Inputs, lead: str | None = None) -> str:
    return text.subtitle(
        lead,
        "; ".join(i.filters) or None,
        _trials_phrase(i),
        _subset_phrase(i),
        data_date=_data_date(i.version),
    )


def _data_date(version: ApiVersion) -> str:
    return date.fromisoformat(version.data_timestamp[:10]).isoformat()


def _top_mark(i: _Inputs, grid: _Grid) -> _Mark:
    """The mark with the highest statistic; a mark that holds no trial has none."""
    measure = i.plan.measure
    if measure is None:
        raise ValueError("Only a statistic has a highest value.")

    def value(mark: _Mark) -> float:
        found = measures.value_of(mark.cell.values, measure)
        return float("-inf") if found is None else float(found)

    return max(grid.marks, key=value)


def _measure_peak_message(i: _Inputs, grid: _Grid) -> str:
    measure = i.plan.measure
    top = _top_mark(i, grid)
    if measure is None:
        raise ValueError("Only a statistic has a highest value.")
    label = text.measure_label(measure.statistic, measure.field)
    value = text.measure_value(measures.value_of(top.cell.values, measure), measure.statistic)
    where = top.x if top.series is None else f"{top.series} in {top.x}"
    return text.measure_top_message(label, where, value, top.cell.trials)


# --- time_series ------------------------------------------------------------------------------------------


def _time_series(i: _Inputs) -> Built:
    axis = i.plan.dimensions[0]
    unit = axis.time_unit or "year"
    window = i.plan.window
    grid = _grid(i)
    series = _series_channel(i, grid)
    x_field = field_name(axis)
    rows = [_grid_row(i, mark, x_field, series, with_share=False) for mark in grid.marks]
    lead = text.window_phrase(axis.spec.title, unit, window.first, window.last) if window else None
    viz = TimeSeries(
        type="time_series",
        title=_time_series_title(i, axis, unit, grid),
        subtitle=_subtitle(i, lead),
        mark=i.choice.options.mark,
        stack="none",
        encoding=TimeSeriesEncoding(
            x=TemporalChannel(
                field=x_field,
                type="temporal",
                title=text.date_axis_title(axis.spec.title, unit),
                time_unit=unit,
            ),
            y=_y_channel(i, text.count_title(axis.spec.key)),
            series=series,
            tooltip=_measured_tooltip(i) if i.plan.measure is not None else [],
        ),
        data=rows,
    )
    return Built(viz, _time_series_message(i, axis.spec.key, grid), len(rows))


def _time_series_title(i: _Inputs, axis: BoundDimension, unit: str, grid: _Grid) -> str:
    split = _split_title(i, grid)
    if i.plan.measure is None:
        return text.time_series_title(axis.spec.key, unit, i.labels, split)
    label = text.measure_label(i.plan.measure.statistic, i.plan.measure.field)
    return text.measure_time_title(label, axis.spec.title, unit, i.labels, split)


def _time_series_message(i: _Inputs, date_key: str, grid: _Grid) -> str:
    if i.plan.measure is not None:
        return _measure_peak_message(i, grid)
    peak = max(grid.marks, key=lambda mark: mark.cell.trials)
    if peak.series is not None:
        return text.series_peak_message(date_key, peak.series, peak.x, peak.cell.trials)
    # A period that is still running is not the one to quote as the latest.
    is_running = len(grid.marks) > 1 and is_partial(i.plan.window, i.version)
    last = grid.marks[-2] if is_running else grid.marks[-1]
    return text.time_series_message(date_key, last.x, last.cell.trials, peak.x, peak.cell.trials)


# --- bar_chart --------------------------------------------------------------------------------------------


def _bar_chart(i: _Inputs) -> Built:
    if not i.plan.dimensions:
        return _compared_totals(i)
    axis = i.plan.dimensions[0]
    grid = _grid(i)
    series = _series_channel(i, grid)
    x = _category_channel(axis, grid.x_labels)
    shown_total = _shown_total(i, grid)
    rows = [
        _grid_row(i, mark, x.field, series, with_share=True, shown_total=shown_total) for mark in grid.marks
    ]
    series_title = _split_title(i, grid) or (series.title if series else None)
    measure = i.plan.measure
    viz = BarChart(
        type="bar_chart",
        title=text.bar_title(axis.spec.title, series_title, i.labels)
        if measure is None
        else text.measure_bar_title(
            text.measure_label(measure.statistic, measure.field), axis.spec.title, series_title, i.labels
        ),
        subtitle=_subtitle(i),
        orientation=i.choice.options.orientation,
        stack=i.choice.options.stack,
        encoding=BarChartEncoding(
            x=x,
            y=_y_channel(i),
            series=series,
            tooltip=_measured_tooltip(i)
            if measure is not None
            else [
                FieldDef(
                    field=SHARE_FIELD,
                    title="Share of trials",
                    type="quantitative",
                    unit=None,
                    format=".1%",
                    href_field=None,
                ),
                *(
                    [
                        FieldDef(
                            field=SHOWN_SHARE_FIELD,
                            title="Share of the bars' total",
                            type="quantitative",
                            unit=None,
                            format=".1%",
                            href_field=None,
                        )
                    ]
                    if shown_total is not None
                    else []
                ),
            ],
        ),
        data=rows,
    )
    if measure is not None:
        return Built(viz, _measure_peak_message(i, grid), len(rows))
    top = max(grid.marks, key=lambda mark: mark.cell.trials)
    if top.series is None:
        message = text.largest_category_message(top.x, top.cell.trials, top.analyzed)
    else:
        message = text.largest_in_series_message(top.x, top.series, top.cell.trials)
    return Built(viz, message, len(rows))


def _shown_total(i: _Inputs, grid: _Grid) -> int | None:
    """The sum of the drawn bars of a state chart: its own denominator, as a state's trials overlap.

    A trial in three states is in three bars, so this is not a number of trials; it is what the bars add
    up to, which a reader asking for "the percentage of the top-N total" means.
    """
    is_plain = len(i.plan.dimensions) == 1 and not i.is_comparison and i.plan.measure is None
    if i.plan.dimensions[0].spec.key != "state" or not is_plain:
        return None
    return sum(mark.cell.trials for mark in grid.marks)


def _measured_row(i: _Inputs, fields: dict[str, Scalar], cell: Cell) -> Datum:
    """A row of a statistic: the value under its own key, and how many trials it is the value of."""
    measure = i.plan.measure
    if measure is not None:
        fields[measures.row_key(measure)] = measures.value_of(cell.values, measure)
    fields[COUNT_FIELD] = cell.trials
    return _datum(fields, i.book.for_cell(cell))


def _total_row(i: _Inputs, frame: Frame, name: str) -> Datum:
    cell = _total_cell(frame)
    return _datum({GROUP_FIELD: name, COUNT_FIELD: cell.trials}, i.book.for_cell(cell))


def _compared_totals(i: _Inputs) -> Built:
    """Rule 4: one bar per compared scope. The scopes may share trials, so the axis is not exclusive."""
    frames = i.shaped.frames
    names = [frame.scope.label or frame.scope.id for frame in frames]
    if i.plan.measure is not None:
        return _compared_measures(i, names)
    rows = [
        _datum(
            {GROUP_FIELD: name, COUNT_FIELD: _total_cell(frame).trials}, i.book.for_cell(_total_cell(frame))
        )
        for frame, name in zip(frames, names, strict=True)
    ]
    title = text.KIND_TITLES.get(i.plan.compare_kind or "", "Group")
    viz = BarChart(
        type="bar_chart",
        title=text.compared_totals_title(i.labels),
        subtitle=_subtitle(i),
        orientation="vertical",
        stack="none",
        encoding=BarChartEncoding(
            x=CategoryChannel(
                field=GROUP_FIELD, type="nominal", title=title, domain=names, sort=None, is_exclusive=False
            ),
            y=_count_channel(),
            series=None,
            tooltip=[],
        ),
        data=rows,
    )
    top = max(zip(names, rows, strict=True), key=lambda pair: pair[1].citation_count)
    return Built(viz, text.compared_totals_message(top[0], top[1].citation_count), len(rows))


def _compared_measures(i: _Inputs, names: list[str]) -> Built:
    """One bar per compared scope, each the statistic of that scope's trials."""
    measure = i.plan.measure
    if measure is None:
        raise ValueError("Only a statistic has compared measures.")
    cells = [_total_cell(frame) for frame in i.shaped.frames]
    rows = [_measured_row(i, {GROUP_FIELD: name}, cell) for name, cell in zip(names, cells, strict=True)]
    label = text.measure_label(measure.statistic, measure.field)
    viz = BarChart(
        type="bar_chart",
        title=text.measure_compared_title(label, i.labels),
        subtitle=_subtitle(i),
        orientation="vertical",
        stack="none",
        encoding=BarChartEncoding(
            x=CategoryChannel(
                field=GROUP_FIELD,
                type="nominal",
                title=text.KIND_TITLES.get(i.plan.compare_kind or "", "Group"),
                domain=names,
                sort=None,
                is_exclusive=False,
            ),
            y=measures.channel(measure),
            series=None,
            tooltip=_measured_tooltip(i),
        ),
        data=rows,
    )
    values = [measures.value_of(cell.values, measure) for cell in cells]
    ranked = [float("-inf") if value is None else float(value) for value in values]
    top = ranked.index(max(ranked))
    message = text.measure_top_message(
        label, names[top], text.measure_value(values[top], measure.statistic), cells[top].trials
    )
    return Built(viz, message, len(rows))


# --- metric and histogram ---------------------------------------------------------------------------------


def _metric(i: _Inputs) -> Built:
    frame = i.shaped.frames[0]
    cell = _total_cell(frame)
    measure = i.plan.measure
    if measure is not None:
        value = measures.value_of(cell.values, measure)
        label = text.measure_label(measure.statistic, measure.field)
        metric = Metric(
            type="metric",
            title=text.measure_compared_title(label, i.labels),
            subtitle=_subtitle(i),
            encoding=MetricEncoding(value=measures.channel(measure)),
            data=[_measured_row(i, {}, cell)],
        )
        message = text.measure_metric_message(
            label, text.measure_value(value, measure.statistic), cell.trials, i.labels
        )
        return Built(metric, message, 1)
    viz = Metric(
        type="metric",
        title=text.compared_totals_title(i.labels),
        subtitle=_subtitle(i),
        encoding=MetricEncoding(value=_count_channel()),
        data=[_datum({COUNT_FIELD: cell.trials}, i.book.for_cell(cell))],
    )
    return Built(viz, text.metric_message(cell.trials, i.labels, i.filters), 1)


def _histogram(i: _Inputs) -> Built:
    axis = i.plan.dimensions[0]
    frame = i.shaped.frames[0]
    bins = vocab.ENROLLMENT_BINS
    cells = [_cell_or_empty(frame, (b.label,), (b.label,)) for b in bins]
    rows = [
        _datum(
            {"bin_start": b.start, "bin_end": b.end, "bin_label": b.label, COUNT_FIELD: cell.trials},
            i.book.for_cell(cell),
        )
        for b, cell in zip(bins, cells, strict=True)
    ]
    viz = Histogram(
        type="histogram",
        title=text.histogram_title(axis.spec.title, i.labels),
        subtitle=_subtitle(i),
        encoding=HistogramEncoding(
            x=QuantitativeChannel(
                field="bin_start",
                type="quantitative",
                title=axis.spec.title,
                unit=text.NUMBER_UNITS.get(axis.spec.key),
                format=",d",
                scale="linear",
            ),
            x2=FieldRef(field="bin_end"),
            y=_count_channel(),
            label=FieldRef(field="bin_label"),
            tooltip=[],
        ),
        data=rows,
    )
    top_bin, top_cell = max(zip(bins, cells, strict=True), key=lambda pair: pair[1].trials)
    return Built(viz, text.histogram_message(top_bin.label, top_cell.trials), len(rows))


# --- scatter_plot, table ----------------------------------------------------------------------------------


def _number_channel(field: str, *, is_log: bool) -> QuantitativeChannel:
    return QuantitativeChannel(
        field=field,
        type="quantitative",
        title=text.NUMBER_TITLES[field],
        unit=text.NUMBER_UNITS[field],
        format=text.NUMBER_FORMATS[field],
        scale="log" if is_log else "linear",
    )


def _scatter_plot(i: _Inputs) -> Built:
    spec = i.plan.rows
    if not isinstance(spec, PointRows):
        raise ValueError("A scatter plot needs a plan that relates two numeric fields.")
    options = i.choice.options
    groups = list(dict.fromkeys(row.group for row in i.shaped.trials if row.group is not None))
    series = _category_channel(spec.color, groups) if spec.color is not None else None
    rows = []
    for trial in i.shaped.trials:
        fields: dict[str, Scalar] = {
            spec.x: trial.values.get(spec.x),
            spec.y: trial.values.get(spec.y),
            "nct_id": trial.nct_id,
            "title": trial.title,
            "url": STUDY_URL.format(nct_id=trial.nct_id),
        }
        if series is not None:
            fields[series.field] = trial.group
        rows.append(_datum(fields, i.book.for_trial(trial.nct_id, trial.evidence)))
    x, y = _number_channel(spec.x, is_log=options.log_x), _number_channel(spec.y, is_log=options.log_y)
    viz = ScatterPlot(
        type="scatter_plot",
        title=text.scatter_title(x.title, y.title, i.labels),
        subtitle=_subtitle(i),
        encoding=ScatterPlotEncoding(
            x=x,
            y=y,
            series=series,
            size=None,
            label=_trial_column("nct_id"),
            tooltip=[_trial_column("title")],
        ),
        data=rows,
    )
    return Built(viz, text.scatter_message(len(rows), x.title, y.title), len(rows))


def _trial_column(name: str) -> FieldDef:
    definitions: dict[str, tuple[str, _ColumnType, str | None, str | None]] = {
        "nct_id": ("NCT ID", "nominal", None, "url"),
        "title": ("Title", "nominal", None, None),
        "phase": ("Phase", "nominal", None, None),
        "overall_status": ("Status", "nominal", None, None),
        "start_date": ("Start date", "temporal", None, None),
        "completion_date": ("Completion date", "temporal", None, None),
        "first_posted_date": ("First posted", "temporal", None, None),
        "enrollment": ("Enrollment", "quantitative", "participants", None),
        "lead_sponsor": ("Lead sponsor", "nominal", None, None),
    }
    title, kind, unit, href = definitions[name]
    return FieldDef(
        field=name,
        title=title,
        type=kind,
        unit=unit,
        format=",d" if kind == "quantitative" else None,
        href_field=href,
    )


def _trial_table(i: _Inputs) -> Built:
    spec = i.plan.rows
    if not isinstance(spec, ListRows):
        raise ValueError("A trial table needs a plan that lists trials.")
    names = ["nct_id", "title", spec.sort_by, *(c for c in _TABLE_COLUMNS if c != spec.sort_by)]
    rows = []
    for trial in i.shaped.trials:
        fields: dict[str, Scalar] = {
            "nct_id": trial.nct_id,
            "title": trial.title,
            "url": STUDY_URL.format(nct_id=trial.nct_id),
            **{name: trial.values.get(name) for name in names[2:]},
        }
        rows.append(_datum(fields, i.book.for_trial(trial.nct_id, trial.evidence)))
    viz = Table(
        type="table",
        title=text.table_title(spec.sort_by, spec.order, i.labels),
        subtitle=_subtitle(i),
        encoding=TableEncoding(columns=[_trial_column(name) for name in names]),
        data=rows,
    )
    total = i.shaped.frames[0].analyzed
    return Built(viz, text.table_message(len(rows), total, spec.sort_by), len(rows))


def _aggregate_as_table(built: Built) -> Built:
    """The rows of a bar chart, time series, histogram or metric, one column per drawn field."""
    viz = built.visualization
    match viz:
        case BarChart() | TimeSeries():
            enc = viz.encoding
            channels = [enc.x, *([enc.series] if enc.series else []), enc.y]
            columns = [_channel_column(c) for c in channels] + list(enc.tooltip)
        case Histogram():
            columns = [
                _channel_column(viz.encoding.x),
                _label_column(viz.encoding.label),
                _channel_column(viz.encoding.y),
            ]
        case Metric():
            columns = [_channel_column(viz.encoding.value)]
        case _:
            raise ValueError("Only aggregated results can be shown as a table of their rows.")
    table = Table(
        type="table",
        title=viz.title,
        subtitle=viz.subtitle,
        encoding=TableEncoding(columns=columns),
        data=viz.data,
    )
    return Built(table, built.message, built.data_points)


def _channel_column(channel: CategoryChannel | TemporalChannel | QuantitativeChannel) -> FieldDef:
    unit = channel.unit if isinstance(channel, QuantitativeChannel) else None
    number_format = channel.format if isinstance(channel, QuantitativeChannel) else None
    return FieldDef(
        field=channel.field,
        title=channel.title,
        type=channel.type,
        unit=unit,
        format=number_format,
        href_field=None,
    )


def _label_column(ref: FieldRef) -> FieldDef:
    return FieldDef(
        field=ref.field, title="Size range", type="nominal", unit=None, format=None, href_field=None
    )


# --- network_graph ----------------------------------------------------------------------------------------


def _network_graph(i: _Inputs) -> Built:
    first, second = i.plan.dimensions[0].spec.key, i.plan.dimensions[1].spec.key
    shaped = i.shaped
    nodes = [
        Node.model_validate(
            {
                "id": node.id,
                "label": node.label,
                "entity_type": node.kind,
                COUNT_FIELD: node.cell.trials,
                **_cited_fields(i.book.for_cell(node.cell)),
            }
        )
        for node in shaped.nodes
    ]
    edges = [
        Edge.model_validate(
            {
                "id": edge.id,
                "source": edge.source.id,
                "target": edge.target.id,
                COUNT_FIELD: edge.cell.trials,
                **_cited_fields(i.book.for_cell(edge.cell)),
            }
        )
        for edge in shaped.edges
    ]
    viz = NetworkGraph(
        type="network_graph",
        title=text.network_title(first, second, i.labels),
        subtitle=_subtitle(i),
        is_directed=False,
        layout=i.choice.options.layout,
        encoding=NetworkEncoding(
            nodes=NetworkNodeEncoding(
                label=FieldRef(field="label"),
                color=CategoryChannel(
                    field="entity_type",
                    type="nominal",
                    title="Node type",
                    domain=list(dict.fromkeys([first, second])),
                    sort=None,
                    is_exclusive=True,
                ),
                size=_count_channel(text.node_size_title()),
                tooltip=[],
            ),
            edges=NetworkEdgeEncoding(weight=_count_channel(text.edge_weight_title()), tooltip=[]),
        ),
        data=NetworkData(nodes=nodes, edges=edges),
    )
    strongest = max(shaped.edges, key=lambda edge: edge.cell.trials)
    message = text.network_message(
        len(nodes), len(edges), strongest.source.label, strongest.target.label, strongest.cell.trials
    )
    return Built(viz, message, len(nodes) + len(edges))


_BUILDERS: dict[str, Callable[[_Inputs], Built]] = {
    "time_series": _time_series,
    "bar_chart": _bar_chart,
    "network_graph": _network_graph,
    "metric": _metric,
    "histogram": _histogram,
    "table": _trial_table,
    "scatter_plot": _scatter_plot,
}
