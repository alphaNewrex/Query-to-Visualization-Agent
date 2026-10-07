"""Shaping what the executors produced into what a chart can show: window, top-N, order, zero-fill, shares.

Everything here is a rule of section 4.11, and every cut is recorded as a truncation item, so a response
can say what it left out. A date or binned axis is never cut, because its periods and bins must stay
contiguous. Compared scopes are shaped together, so that every series shows the same categories in the
same order.
"""

import dataclasses
import itertools
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal

from ctviz.catalog.fields import BoundDimension, Bucket, Window
from ctviz.contract.response import Note, Outcome, StrategyStep, TruncationItem
from ctviz.ctgov import essie
from ctviz.ctgov.essie import Expr
from ctviz.ctgov.study import Study
from ctviz.engine.execute import EngineResult
from ctviz.engine.frame import Cell, Frame
from ctviz.engine.lower import EnginePlan, ListRows, PointRows
from ctviz.engine.network import prune_network
from ctviz.engine.rows import RowsResult

OTHER_KEY: Final = "other"  # the key of the cell that sums the categories a top-N left out
MAX_POINTS: Final = 500


@dataclass(frozen=True)
class ShapedFrame:
    """The cells of one scope as they are drawn: in order, with the full grid, cut to what fits."""

    frame: Frame  # the counts the cells were taken from
    cells: tuple[Cell, ...]  # axis-major, series in order; the links of a network
    nodes: tuple[tuple[Cell, ...], ...] = ()  # a network's nodes, one tuple per side

    def share(self, cell: Cell) -> float:
        """The cell's trials over the analysed trials of its series."""
        return cell.trials / self.frame.analyzed if self.frame.analyzed else 0.0


@dataclass(frozen=True)
class ShapedResult:
    frames: tuple[ShapedFrame, ...]
    rows: tuple[RowsResult, ...]
    window: Window | None  # leading empty periods trimmed
    warnings: tuple[Note, ...]
    assumptions: tuple[str, ...]  # sentences the shaping adds to `meta.assumptions`
    truncation: tuple[TruncationItem, ...]
    steps: tuple[StrategyStep, ...]
    outcome: Outcome | None  # set when the shaped data cannot be drawn


def shape(result: EngineResult, plan: EnginePlan) -> ShapedResult:
    if plan.rows is not None:
        return _shape_rows(result, plan)
    if plan.relation == "network":
        return _shape_networks(result, plan)
    return _shape_grids(result, plan)


# --- per-trial rows ---------------------------------------------------------------------------------------


def _shape_rows(result: EngineResult, plan: EnginePlan) -> ShapedResult:
    shaped: list[RowsResult] = []
    truncation: list[TruncationItem] = []
    warnings = list(result.warnings)
    for rows in result.rows:
        if isinstance(plan.rows, PointRows) and len(rows.rows) > MAX_POINTS:
            ranked = sorted(rows.rows, key=lambda row: _first_posted(row.study), reverse=True)
            cut = len(rows.rows) - MAX_POINTS
            rows = dataclasses.replace(rows, rows=ranked[:MAX_POINTS], excluded=dict(rows.excluded))
            rows.exclude(
                "beyond_plotted_points", f"Not among the {MAX_POINTS} most recently first-posted", cut
            )
            truncation.append(
                TruncationItem(
                    scope="points",
                    shown=MAX_POINTS,
                    total=MAX_POINTS + cut,
                    rule=f"The {MAX_POINTS} most recently first-posted trials that have both values.",
                )
            )
            warnings.append(
                Note(
                    code="recent_subset",
                    message=f"Only the {MAX_POINTS} most recently first-posted of {MAX_POINTS + cut:,} "
                    "trials are plotted.",
                )
            )
        elif isinstance(plan.rows, ListRows) and rows.matched > len(rows.rows):
            truncation.append(
                TruncationItem(
                    scope="rows",
                    shown=len(rows.rows),
                    total=rows.matched,
                    rule=f"The first {len(rows.rows)} trials in the requested order.",
                )
            )
        shaped.append(rows)
    return ShapedResult((), tuple(shaped), None, tuple(warnings), (), tuple(truncation), result.steps, None)


def _first_posted(study: Study) -> tuple[str, str]:
    """Most recent first, ties by NCT ID, when sorted in reverse."""
    return (study.first_post_date.date if study.first_post_date is not None else "", study.nct_id)


# --- networks ---------------------------------------------------------------------------------------------


def _shape_networks(result: EngineResult, plan: EnginePlan) -> ShapedResult:
    frames: list[ShapedFrame] = []
    truncation: list[TruncationItem] = []
    warnings = list(result.warnings)
    assumptions: list[str] = []
    sparse = True
    for frame in result.frames:
        pruned = prune_network(frame, plan)
        frames.append(ShapedFrame(frame, pruned.links, pruned.nodes))
        truncation.extend(pruned.truncation)
        warnings.extend(pruned.warnings)
        assumptions.extend(pruned.assumptions)
        sparse = sparse and pruned.is_sparse
    outcome = None
    if sparse:
        message = "Too few trials list two of these together to draw a network."
        warnings.append(Note(code="insufficient_cooccurrence", message=message))
        outcome = Outcome(kind="no_data", reason="insufficient_cooccurrence", message=message)
    return ShapedResult(
        tuple(frames), (), None, tuple(warnings), tuple(assumptions), tuple(truncation), result.steps, outcome
    )


# --- grids of counts --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Axis:
    """The categories of one dimension as drawn: in order, with what a top-N or series cap merged."""

    dimension: BoundDimension
    keys: tuple[str, ...]  # in drawing order, `OTHER_KEY` last when present
    merged: tuple[str, ...]  # the keys that `OTHER_KEY` stands for
    labels: dict[str, str]
    exprs: dict[str, Expr | None]

    def sources(self, key: str) -> tuple[str, ...]:
        return self.merged if key == OTHER_KEY else (key,)


def _shape_grids(result: EngineResult, plan: EnginePlan) -> ShapedResult:
    if not plan.dimensions:
        frames = tuple(ShapedFrame(frame, (_only_cell(frame),)) for frame in result.frames)
        return ShapedResult(frames, (), result.window, result.warnings, (), (), result.steps, None)

    truncation: list[TruncationItem] = []
    window = result.window
    axis = _axis(plan.dimensions[0], result, plan, 0, window, plan.top_n, truncation, "categories")
    if axis.dimension.spec.kind == "date" and window is not None and plan.public.filters.year_from is None:
        axis, window = _trim_leading_empty(axis, result, window)
    series = None
    if plan.relation == "series":
        series = _axis(plan.dimensions[1], result, plan, 1, window, plan.max_series, truncation, "series")
    frames = tuple(
        ShapedFrame(frame, _grid(frame, axis, series, plan.citations_per_datum)) for frame in result.frames
    )
    return ShapedResult(frames, (), window, result.warnings, (), tuple(truncation), result.steps, None)


def _axis(
    dimension: BoundDimension,
    result: EngineResult,
    plan: EnginePlan,
    position: int,
    window: Window | None,
    limit: int,
    truncation: list[TruncationItem],
    scope: Literal["categories", "series"],
) -> _Axis:
    spec = dimension.spec
    totals: Counter[str] = Counter()
    labels: dict[str, str] = {}
    for frame in result.frames:
        for cell in frame.cells.values():
            totals[cell.key[position]] += cell.trials
            labels[cell.key[position]] = cell.labels[position]
    buckets: Sequence[Bucket] = spec.buckets(dimension, window) if spec.buckets is not None else ()
    labels = {**{bucket.key: bucket.label for bucket in buckets}, **labels}
    exprs = {bucket.key: bucket.expr for bucket in buckets}

    is_natural = spec.is_ordinal or spec.kind in ("date", "number")
    if is_natural:
        listed = [bucket.key for bucket in buckets]
        keys = [*listed, *sorted(key for key in totals if key not in listed)]
    else:
        keys = sorted(
            (key for key, total in totals.items() if total > 0), key=lambda key: (-totals[key], key)
        )

    merged: tuple[str, ...] = ()
    is_cut_allowed = spec.kind in ("category", "entity")
    if is_cut_allowed and len(keys) > limit:
        # An exclusive dimension keeps one place for the sum of the rest; a multi-valued one cannot sum.
        keep = limit - 1 if spec.is_exclusive and scope == "series" else limit
        kept = set(sorted(keys, key=lambda key: (-totals[key], key))[:keep])
        rest_summed = ", the rest summed as Other" if spec.is_exclusive else ""
        truncation.append(
            TruncationItem(
                scope=scope,
                shown=keep,
                total=len(keys),
                rule=f"The {keep} largest by trials{rest_summed}.",
            )
        )
        dropped = tuple(key for key in keys if key not in kept)
        keys = [key for key in keys if key in kept]
        if spec.is_exclusive:
            merged = dropped
            keys.append(OTHER_KEY)
            labels[OTHER_KEY] = f"Other ({len(dropped)} more)"
    return _Axis(dimension, tuple(keys), merged, labels, exprs)


def _trim_leading_empty(axis: _Axis, result: EngineResult, window: Window) -> tuple[_Axis, Window]:
    """Drop the periods before the first trial: a default window reaches back further than most data."""
    populated = {cell.key[0] for frame in result.frames for cell in frame.cells.values() if cell.trials}
    first = next((position for position, key in enumerate(axis.keys) if key in populated), 0)
    if first == 0:
        return axis, window
    keys = axis.keys[first:]
    return dataclasses.replace(axis, keys=keys), Window(window.unit, keys[0], window.last)


def _grid(frame: Frame, axis: _Axis, series: _Axis | None, sample_size: int) -> tuple[Cell, ...]:
    cells = []
    for x in axis.keys:
        for s in series.keys if series is not None else (None,):
            axes = [(axis, x), *([(series, s)] if series is not None and s is not None else [])]
            cells.append(_cell(frame, axes, sample_size))
    return tuple(cells)


def _cell(frame: Frame, axes: Sequence[tuple[_Axis, str]], sample_size: int) -> Cell:
    """The cell of one position of the grid: the frame's own, a sum for `Other`, or a zero."""
    key = tuple(position_key for _, position_key in axes)
    labels = tuple(axis.labels.get(position_key, position_key) for axis, position_key in axes)
    parts = [
        frame.cells[combo]
        for combo in itertools.product(*(axis.sources(position_key) for axis, position_key in axes))
        if combo in frame.cells
    ]
    if len(parts) == 1 and OTHER_KEY not in key:
        return parts[0]
    is_merged = OTHER_KEY in key
    exprs = [axis.exprs.get(position_key) for axis, position_key in axes]
    expr = None if is_merged or None in exprs else essie.and_(*(e for e in exprs if e is not None))
    sample = sorted((trial for part in parts for trial in part.sample), key=lambda t: t.rank, reverse=True)
    return Cell(
        key=key,
        labels=labels,
        trials=sum(part.trials for part in parts),
        sample=sample[:sample_size],
        expr=expr,
    )


def _only_cell(frame: Frame) -> Cell:
    """The single number of a scope that counts trials by nothing; a scope without trials counts zero."""
    return frame.cells.get((), Cell(key=()))
