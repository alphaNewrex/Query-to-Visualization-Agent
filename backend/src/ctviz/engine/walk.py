"""The walk executor: read the trials of a scope page by page and group them here.

A walk also serves per-trial rows. What it found out about the scope beyond the trials themselves (trials
left out by a presence push-down, a data refresh in the middle) is returned as a `WalkRead`, so that a
`Frame` and a `RowsResult` account for them the same way. A walk reads every trial of its scope: the number
of trials read is checked against the registry's count, and a difference is reported, never hidden.
"""

import dataclasses
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
from ctviz.engine.frame import Frame
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


async def read_scope(run: ScopeRun, plan: EnginePlan, client: Registry, ctx: RequestLog) -> WalkRead:
    """Walk every trial of a scope."""
    walked = run.walk_scope or run.scope
    result = await client.walk(walked.params(), ctx, fields=run.fields)
    read = len(result.studies)
    unread: list[Unread] = []
    warnings: list[Note] = []
    if run.walk_scope is not None:
        # The push-down removed trials the walk could not have grouped; the registry's total says how many.
        unread.append((*_pushed_reason(plan), max(0, run.matched - result.total)))
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
    read = await read_scope(run, plan, client, ctx)
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
        _compose_source_urls(frame, plan, window)
    return frame


def _compose_source_urls(frame: Frame, plan: EnginePlan, window: Window | None) -> None:
    """Give every cell of a full walk the URL that returns exactly its trials, where one search can.

    The URL is composed and not called. It has the shape of a fan-out's sample call, so a reader who
    opens it sees the cell's count as `totalCount` and a few of its trials.
    """
    dims = frame.dims
    if plan.measure is not None or not dims or len({d.spec.key for d in dims}) < len(dims):
        return  # a statistic's cell holds trials that have a value; no search returns exactly those
    fields = projection(plan, frame.scope)
    sort = "@relevance" if frame.scope.terms else "StudyFirstPostDate:desc"
    for cell in frame.cells.values():
        exprs = [_bucket_expr(dimension, key, window) for dimension, key in zip(dims, cell.key, strict=True)]
        if any(expr is None for expr in exprs):
            continue
        cell.expr = essie.and_(*(expr for expr in exprs if expr is not None))
        cell.source_url = canonical_url(
            _PUBLIC_API,
            frame.scope.params().narrowed_by(cell.expr),
            count_total=True,
            page_size=max(1, frame.sample_size),
            fields=fields,
            sort=sort,
        )


def _bucket_expr(dimension: BoundDimension, key: str, window: Window | None) -> Expr | None:
    """The server expression of one value of a dimension, or None when the registry cannot select it."""
    spec = dimension.spec
    if spec.buckets is not None:
        return next((bucket.expr for bucket in spec.buckets(dimension, window) if bucket.key == key), None)
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


def _pushed_reason(plan: EnginePlan) -> tuple[str, str]:
    pushed = next(dimension for dimension in plan.dimensions if dimension.spec.presence is not None)
    return missing_reason(pushed)
