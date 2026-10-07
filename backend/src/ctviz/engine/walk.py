"""The walk executor: read the trials of a scope page by page and group them here.

A walk also serves per-trial rows. What it found out about the scope beyond the trials themselves (trials
left out by a presence push-down, a data refresh in the middle) is returned as a `WalkRead`, so that a
`Frame` and a `RowsResult` account for them the same way. A walk reads every trial of its scope: the number
of trials read is checked against the registry's count, and a difference is reported, never hidden.
"""

import dataclasses
import itertools
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final

from ctviz.catalog.fields import BoundDimension, FieldContext, FieldContexts, Window
from ctviz.contract.response import Note
from ctviz.ctgov import essie
from ctviz.ctgov.client import RequestLog
from ctviz.ctgov.essie import Expr
from ctviz.ctgov.params import Scope, canonical_url
from ctviz.ctgov.study import Study
from ctviz.engine.aggregate import aggregate, missing_reason
from ctviz.engine.evidence import projection
from ctviz.engine.fanout import Probe, disjoint, window_probes
from ctviz.engine.frame import Cell, Frame
from ctviz.engine.gather import gather
from ctviz.engine.lower import EnginePlan
from ctviz.engine.registry import Registry
from ctviz.engine.strategy import ScopeRun

# (reason, message, count) of trials the walk never saw.
Unread = tuple[str, str, int]
# A composed `source_url` is for a reader to open, so it names the public registry whatever the client calls.
_PUBLIC_API: Final = "https://clinicaltrials.gov/api/v2"


@dataclass(frozen=True)
class WalkRead:
    """The trials a walk read and what is known about the ones it did not."""

    studies: tuple[Study, ...]
    matched: int  # every trial of the scope: those read plus those in `unread`
    unread: tuple[Unread, ...]
    warnings: tuple[Note, ...]


async def read_scope(
    run: ScopeRun,
    plan: EnginePlan,
    client: Registry,
    ctx: RequestLog,
    window: Window | None = None,
) -> WalkRead:
    """Walk every trial of a scope."""
    walked = run.walk_scope or run.scope
    result = await client.walk(walked.params(), ctx, fields=run.fields)
    read = len(result.studies)
    unread: list[Unread] = []
    warnings: list[Note] = []
    if run.walk_scope is not None:
        # The push-down removed trials the walk could not have grouped; the registry's total says how many,
        # and counts say which reason each would have been given in a walk of the whole scope.
        unread.extend(await _unread_reasons(run, plan, window, result.total, client, ctx))
    if not result.is_consistent:
        warnings.append(
            Note(
                code="walk_count_mismatch",
                message=f"The registry reported {result.total:,} trials but {read:,} were read, "
                "probably because its data changed during the request.",
            )
        )
    matched = read + sum(count for _, _, count in unread)
    return WalkRead(result.studies, matched, tuple(item for item in unread if item[2]), tuple(warnings))


async def walk_frame(
    run: ScopeRun, plan: EnginePlan, window: Window | None, client: Registry, ctx: RequestLog
) -> Frame:
    """Walk a scope and group what was read into a frame."""
    read = await read_scope(run, plan, client, ctx, window)
    windowed = dataclasses.replace(plan, window=window)
    frame = aggregate(
        read.studies, windowed, run.scope, _fit(plan, read.studies, run.scope), plan.citations_per_datum
    )
    frame.strategy = run.strategy
    frame.matched = read.matched
    frame.warnings.extend(read.warnings)
    for reason, message, count in read.unread:
        frame.exclude(reason, message, count)
    if run.strategy == "walk" and not read.warnings:
        await _compose_source_urls(frame, plan, window, client, ctx)
    return frame


async def _compose_source_urls(
    frame: Frame, plan: EnginePlan, window: Window | None, client: Registry, ctx: RequestLog
) -> None:
    """Give every cell of a full walk the URL that returns exactly its trials, where one search can.

    The URL is composed and not called. It has the shape of a fan-out's sample call, so a reader who
    opens it sees the cell's count as `totalCount` and a few of its trials. A cell of a closed field that
    no trial has gets one too, as a fan-out's cell does, and its count is zero. Where the registry matches a
    field more loosely than the walk groups it (a state), the search is checked with one count call and
    given only if it returns the cell's own number of trials.
    """
    dims = frame.dims
    if plan.measure is not None or not dims or len({d.spec.key for d in dims}) < len(dims):
        return  # a statistic's cell holds trials that have a value; no search returns exactly those
    for cell in frame.cells.values():
        _compose(frame, plan, cell, window)
    await _check_loose_cells(frame, plan, client, ctx)
    _fill_empty_cells(frame, plan, window)


def _compose(frame: Frame, plan: EnginePlan, cell: Cell, window: Window | None) -> None:
    exprs = [
        _bucket_expr(dimension, key, window, frame.scope)
        for dimension, key in zip(frame.dims, cell.key, strict=True)
    ]
    if any(expr is None for expr in exprs):
        return
    cell.expr = essie.and_(*(expr for expr in exprs if expr is not None))
    cell.source_url = _url_of(frame, plan, cell.expr)


def _url_of(frame: Frame, plan: EnginePlan, expr: Expr) -> str:
    return canonical_url(
        _PUBLIC_API,
        frame.scope.params().narrowed_by(expr),
        count_total=True,
        page_size=max(1, frame.sample_size),
        fields=projection(plan, frame.scope),
        sort="@relevance" if frame.scope.terms else "StudyFirstPostDate:desc",
    )


async def _check_loose_cells(frame: Frame, plan: EnginePlan, client: Registry, ctx: RequestLog) -> None:
    """Keep the composed search of a loosely matched cell only when the registry counts the cell's trials.

    Only the cells that can be drawn are checked: those of the `top_n` largest values of the field. The
    search of any other cell is withheld, as no URL is better than one that returns other trials.
    """
    positions = [p for p, dimension in enumerate(frame.dims) if dimension.spec.is_loosely_matched]
    if not positions:
        return
    drawn: set[tuple[int, str]] = set()
    for position in positions:
        ranked = sorted(frame.marginals[position].items(), key=lambda item: (-item[1].trials, item[0]))
        drawn.update((position, key) for key, _ in ranked[: plan.top_n])
    to_check: list[Cell] = []
    for cell in frame.cells.values():
        if cell.expr is not None and all((p, cell.key[p]) in drawn for p in positions):
            to_check.append(cell)
        else:
            _withhold(cell)

    def checker(cell: Cell) -> Callable[[], Awaitable[int]]:
        expr = cell.expr
        assert expr is not None
        return lambda: client.count(frame.scope.params().narrowed_by(expr), ctx, origin="execution")

    counts = await gather([checker(cell) for cell in to_check])
    for cell, counted in zip(to_check, counts, strict=True):
        if counted != cell.trials:
            _withhold(cell)


def _withhold(cell: Cell) -> None:
    cell.expr = None
    cell.source_url = None


def _fill_empty_cells(frame: Frame, plan: EnginePlan, window: Window | None) -> None:
    """Add the cells of a closed grid that no trial fell into, with their search and a count of zero."""
    lists = []
    for dimension in frame.dims:
        spec = dimension.spec
        if spec.buckets is None:
            return  # an open field has no list of values to fill
        lists.append(spec.buckets(dimension, window))
    for combo in itertools.product(*lists):
        key = tuple(bucket.key for bucket in combo)
        if key in frame.cells or any(bucket.expr is None for bucket in combo):
            continue
        expr = essie.and_(*(bucket.expr for bucket in combo if bucket.expr is not None))
        frame.cells[key] = Cell(
            key=key,
            labels=tuple(bucket.label for bucket in combo),
            expr=expr,
            source_url=_url_of(frame, plan, expr),
        )


def _bucket_expr(dimension: BoundDimension, key: str, window: Window | None, scope: Scope) -> Expr | None:
    """The server expression of one value of a dimension, or None when the registry cannot select it."""
    spec = dimension.spec
    if spec.buckets is not None:
        return next((bucket.expr for bucket in spec.buckets(dimension, window) if bucket.key == key), None)
    if spec.bucket_in_scope is not None:
        bucket = spec.bucket_in_scope(key, scope)
    else:
        bucket = spec.bucket_for(key) if spec.bucket_for is not None else None
    return bucket.expr if bucket is not None else None


def _fit(plan: EnginePlan, studies: tuple[Study, ...], scope: Scope) -> FieldContexts:
    """The state some fields fit to the whole result set, or to the scope, before values are extracted."""
    contexts: dict[str, FieldContext] = {}
    for dimension in plan.dimensions:
        spec = dimension.spec
        if spec.prepare_in_scope is not None:
            contexts[spec.key] = spec.prepare_in_scope(studies, scope)
        elif spec.prepare is not None:
            contexts[spec.key] = spec.prepare(studies)
    return contexts


async def _unread_reasons(
    run: ScopeRun, plan: EnginePlan, window: Window | None, kept: int, client: Registry, ctx: RequestLog
) -> list[Unread]:
    """Why the push-down left trials unread, with the reason a walk of the whole scope would give each.

    A walk excludes a trial for the first reason that applies, taking the dimensions in order: no value,
    then (on a date axis) before or after the window. The trials the push-down removed are given the same
    reasons here, by the same disjoint counts a fan-out makes. A scope pushed down on one dimension with
    no window has a single reason and costs no request; otherwise each reason but the last is one count.
    """
    removed = max(0, run.matched - kept)
    if not removed:
        return []
    pushed = (_presence_of(dimension) for dimension in plan.dimensions if _is_pushed(dimension))
    presences = list(dict.fromkeys(pushed))
    stages: list[Probe] = []
    for dimension in plan.dimensions:
        if _is_pushed(dimension):
            reason, message = missing_reason(dimension)
            stages.append((reason, message, _absent(dimension)))
        if dimension.spec.kind == "date" and window is not None:
            stages.extend(window_probes(run.scope, dimension.spec.pieces[0], window))
    stages = disjoint(stages)
    if len(stages) == 1:
        return [(stages[0][0], stages[0][1], removed)]
    is_removed = essie.not_(essie.and_(*presences))
    unread: list[Unread] = []
    left = removed
    for reason, message, expr in stages[:-1]:
        narrowed = run.scope.params().narrowed_by(essie.and_(expr, is_removed))
        count = min(left, await client.count(narrowed, ctx, origin="execution"))
        unread.append((reason, message, count))
        left -= count
    last_reason, last_message, _ = stages[-1]
    unread.append((last_reason, last_message, left))
    return unread


def _is_pushed(dimension: BoundDimension) -> bool:
    return dimension.spec.presence is not None


def _absent(dimension: BoundDimension) -> Expr:
    """The trials that lack the dimension's value, as the negation of its presence expression."""
    piece, presence = dimension.spec.pieces[0], _presence_of(dimension)
    return essie.missing(piece) if presence == essie.not_(essie.missing(piece)) else essie.not_(presence)


def _presence_of(dimension: BoundDimension) -> Expr:
    presence = dimension.spec.presence
    if presence is None:
        raise ValueError("Only a dimension with a presence expression is pushed down.")
    return presence
