"""Running an execution plan against the registry: every scope by its own strategy, all at once.

Each strategy returns the same kind of result, a `Frame` of cells or the rows of a per-trial plan, so what
comes after does not depend on how the trials were fetched.
"""

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

from ctviz.catalog.fields import Window
from ctviz.contract.response import Note, StrategyStep, UpstreamRequest
from ctviz.ctgov.client import RequestLog
from ctviz.ctgov.study import Study
from ctviz.engine.fanout import fan_out
from ctviz.engine.frame import Frame
from ctviz.engine.gather import gather
from ctviz.engine.lower import EnginePlan
from ctviz.engine.registry import Registry
from ctviz.engine.rows import RowsResult, trial_rows
from ctviz.engine.strategy import ExecutionPlan, ScopeRun
from ctviz.engine.walk import read_scope, walk_frame


@dataclass(frozen=True)
class EngineResult:
    """What the registry gave: one frame per scope, or the rows of each scope for a per-trial plan."""

    frames: tuple[Frame, ...]
    rows: tuple[RowsResult, ...]
    window: Window | None  # the periods of a date axis that were counted
    warnings: tuple[Note, ...]  # the strategy's and every frame's, each once
    steps: tuple[StrategyStep, ...]  # how each scope was fetched, for `meta.interpretation.strategy`


async def execute_plan(
    plan: EnginePlan, xp: ExecutionPlan, client: Registry, ctx: RequestLog
) -> EngineResult:
    counters = [_CountingLog(ctx) for _ in xp.runs]
    outputs = await gather(
        [_runner(run, plan, xp, client, counter) for run, counter in zip(xp.runs, counters, strict=True)]
    )
    frames = tuple(output for output in outputs if isinstance(output, Frame))
    rows = tuple(output for output in outputs if isinstance(output, RowsResult))
    steps = tuple(
        StrategyStep(
            series=run.scope.label, name=run.strategy, reason=run.reason, upstream_requests=counter.requests
        )
        for run, counter in zip(xp.runs, counters, strict=True)
    )
    found = [note for output in outputs for note in output.warnings]
    return EngineResult(frames, rows, xp.window, _unique([*xp.warnings, *found]), steps)


def _runner(
    run: ScopeRun, plan: EnginePlan, xp: ExecutionPlan, client: Registry, ctx: RequestLog
) -> Callable[[], Awaitable[Frame | RowsResult]]:
    return lambda: _execute_run(run, plan, xp.window, client, ctx)


async def _execute_run(
    run: ScopeRun, plan: EnginePlan, window: Window | None, client: Registry, ctx: RequestLog
) -> Frame | RowsResult:
    if run.strategy == "none":
        return Frame(scope=run.scope, dims=plan.dimensions, sample_size=0, strategy="none")
    if plan.rows is not None:
        return await _rows(run, plan, client, ctx)
    if run.strategy == "count_fan_out":
        return await fan_out(run.scope, plan, matched=run.matched, window=window, client=client, ctx=ctx)
    return await walk_frame(run, plan, window, client, ctx)


async def _rows(run: ScopeRun, plan: EnginePlan, client: Registry, ctx: RequestLog) -> RowsResult:
    if run.strategy == "sorted_page" and run.sort is not None:
        page = await client.sorted_page(
            run.scope.params(), ctx, fields=run.fields, sort=run.sort, page_size=run.limit
        )
        result = trial_rows(page.studies, plan, run.scope)
        result.matched = page.total
        if page.total > result.seen:
            result.exclude(
                "beyond_listed_rows", f"Not among the {result.seen} listed", page.total - result.seen
            )
    else:
        read = await read_scope(run, plan, client, ctx)
        result = trial_rows(read.studies, plan, run.scope)
        result.matched = read.matched
        result.warnings.extend(read.warnings)
        for reason, message, count in read.unread:
            result.exclude(reason, message, count)
    result.strategy = run.strategy
    return result


class _CountingLog:
    """Passes a scope's requests on to the request's log, counting them for the strategy step."""

    def __init__(self, inner: RequestLog) -> None:
        self._inner = inner
        self.requests = 0

    def log_request(self, entry: UpstreamRequest) -> None:
        self.requests += 1
        self._inner.log_request(entry)

    def note_studies(self, studies: Iterable[Study]) -> None:
        self._inner.note_studies(studies)


def _unique(notes: Iterable[Note]) -> tuple[Note, ...]:
    return tuple(dict.fromkeys(notes))
