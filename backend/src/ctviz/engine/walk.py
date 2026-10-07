"""The walk executor: read the trials of a scope page by page and group them here.

A walk also serves per-trial rows. What it found out about the scope beyond the trials themselves (trials
left out by a presence push-down, trials beyond a cap, a data refresh in the middle) is returned as a
`WalkRead`, so that a `Frame` and a `RowsResult` account for them the same way.
"""

import dataclasses
from dataclasses import dataclass
from datetime import date
from typing import Final

from ctviz.catalog.fields import BoundDimension, FieldContexts, Window
from ctviz.contract.response import Note
from ctviz.ctgov import essie
from ctviz.ctgov.client import RequestLog
from ctviz.ctgov.essie import Expr
from ctviz.ctgov.params import canonical_url
from ctviz.ctgov.study import Study
from ctviz.engine.aggregate import aggregate, missing_reason
from ctviz.engine.evidence import projection
from ctviz.engine.frame import Frame, SubsetInfo
from ctviz.engine.lower import EnginePlan
from ctviz.engine.registry import Registry
from ctviz.engine.strategy import ScopeRun

# (reason, message, count) of trials the walk never saw.
Unread = tuple[str, str, int]
_FEWER_THAN_TWO: Final = ("fewer_than_two_interventions", "Fewer than two interventions listed")
# A composed `source_url` is for a reader to open, so it names the public registry whatever the client calls.
_PUBLIC_API: Final = "https://clinicaltrials.gov/api/v2"


@dataclass(frozen=True)
class WalkRead:
    """The trials a walk read and what is known about the ones it did not."""

    studies: tuple[Study, ...]
    matched: int  # every trial of the scope: those read plus those in `unread`
    unread: tuple[Unread, ...]
    subset: SubsetInfo | None  # for a capped walk
    warnings: tuple[Note, ...]


async def read_scope(run: ScopeRun, plan: EnginePlan, client: Registry, ctx: RequestLog) -> WalkRead:
    """Walk a scope up to the run's limit, newest first when it is capped."""
    walked = run.walk_scope or run.scope
    result = await client.walk(walked.params(), ctx, fields=run.fields, limit=run.limit, sort=run.sort)
    read = len(result.studies)
    unread: list[Unread] = []
    warnings: list[Note] = []
    if run.walk_scope is not None:
        # The push-down removed trials the walk could not have grouped; the registry's total says how many.
        unread.append((*_pushed_reason(plan, run), max(0, run.matched - result.total)))
    if result.is_truncated:
        unread.append(
            (
                "outside_recent_subset",
                f"Not among the {read:,} most recently first-posted trials",
                result.total - read,
            )
        )
    elif result.total != read:
        warnings.append(
            Note(
                code="walk_count_mismatch",
                message=f"The registry reported {result.total:,} trials but {read:,} were read, "
                "probably because its data changed during the request.",
            )
        )
    matched = read + sum(count for _, _, count in unread)
    subset = _subset(result.studies) if run.strategy == "capped_walk" else None
    return WalkRead(
        result.studies, matched, tuple(item for item in unread if item[2]), subset, tuple(warnings)
    )


async def walk_frame(
    run: ScopeRun, plan: EnginePlan, window: Window | None, client: Registry, ctx: RequestLog
) -> Frame:
    """Walk a scope and group what was read into a frame."""
    read = await read_scope(run, plan, client, ctx)
    windowed = dataclasses.replace(plan, window=window)
    frame = aggregate(read.studies, windowed, run.scope, _fit(plan, read.studies), plan.citations_per_datum)
    frame.strategy = run.strategy
    frame.matched = read.matched
    frame.subset = read.subset
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


def _fit(plan: EnginePlan, studies: tuple[Study, ...]) -> FieldContexts:
    """The state some fields fit to the whole result set before values are extracted."""
    return {
        dimension.spec.key: dimension.spec.prepare(studies)
        for dimension in plan.dimensions
        if dimension.spec.prepare is not None
    }


def _pushed_reason(plan: EnginePlan, run: ScopeRun) -> tuple[str, str]:
    if (
        run.strategy == "capped_walk"
        and plan.relation == "network"
        and {d.spec.key for d in plan.dimensions} == {"drug"}
    ):
        return _FEWER_THAN_TWO
    pushed = next(dimension for dimension in plan.dimensions if dimension.spec.presence is not None)
    return missing_reason(pushed)


def _subset(studies: tuple[Study, ...]) -> SubsetInfo | None:
    posted = [_as_day(study.first_post_date.date) for study in studies if study.first_post_date is not None]
    return SubsetInfo(len(studies), min(posted), max(posted)) if posted else None


def _as_day(text: str) -> date:
    """A registry date: `2026-03-05`, or `2026-03` which stands for the first of the month."""
    return date.fromisoformat(text if len(text) == 10 else f"{text}-01")
