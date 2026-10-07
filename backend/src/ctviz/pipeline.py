"""The request path after validation: plan, resolve, choose a strategy, execute, shape, build, verify.

No HTTP here; `api/` calls `answer` and `execute`, and so do the examples recorder and the tests.
"""

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime

import structlog
from structlog.typing import FilteringBoundLogger

from ctviz.applied import applied_filters
from ctviz.catalog.countries import CountryTable
from ctviz.catalog.fields import FieldSpec
from ctviz.contract.invariants import check_invariants
from ctviz.contract.request import QueryRequest
from ctviz.contract.response import (
    AppliedFilters,
    CacheInfo,
    EntityResolution,
    Note,
    Outcome,
    QueryResponse,
    Source,
    StrategyStep,
    Timing,
    VisualizationResponse,
)
from ctviz.ctgov.client import ApiVersion, CtGovClient
from ctviz.ctgov.context import RequestContext
from ctviz.engine.execute import execute_plan
from ctviz.engine.handoff import present
from ctviz.engine.lower import lower_plan
from ctviz.engine.overlap import shared_trials
from ctviz.engine.resolve import EntityResolver, resolve_entities
from ctviz.engine.shape import shape
from ctviz.engine.strategy import Limits, choose_strategy
from ctviz.errors import InvariantViolation
from ctviz.planning.service import PlannedQuery, PlanService
from ctviz.settings import Settings
from ctviz.viz.meta import MetaContext, build_response, outcome_response

_log: FilteringBoundLogger = structlog.get_logger()
_NO_CACHE = CacheInfo(is_plan_cached=False, is_response_cached=False, cached_at=None)
_INVARIANT_MESSAGE = "The answer failed an internal consistency check and was withheld. Quote the request id."


@dataclass(frozen=True)
class Deps:
    settings: Settings
    ctgov: CtGovClient
    plans: PlanService
    resolver: EntityResolver
    catalog: Mapping[str, FieldSpec]
    countries: CountryTable
    clock: Callable[[], datetime]


class _Stopwatch:
    """Milliseconds per stage of one request."""

    def __init__(self, plan_ms: int) -> None:
        self.plan_ms = plan_ms
        self.resolve_ms = 0
        self.fetch_ms = 0
        self._started = time.perf_counter()
        self._mark = self._started

    def lap(self) -> int:
        now = time.perf_counter()
        elapsed, self._mark = round((now - self._mark) * 1000), now
        return elapsed

    def timing(self) -> Timing:
        total = self.plan_ms + round((time.perf_counter() - self._started) * 1000)
        return Timing(
            total_ms=total,
            plan_ms=self.plan_ms,
            resolve_ms=self.resolve_ms,
            fetch_ms=self.fetch_ms,
            build_ms=0,
        )


async def answer(request: QueryRequest, deps: Deps, ctx: RequestContext) -> QueryResponse:
    """Stages 2 to 9: plan the question, then run the plan."""
    started = time.perf_counter()
    first_request = len(ctx.requests)
    planned = await deps.plans.produce(request, deps.clock().date(), ctx)
    plan_ms = round((time.perf_counter() - started) * 1000)
    ctx.add_step("plan", planned.plan.interpretation, plan_ms, first_request)
    return await execute(planned, deps, ctx, plan_ms=plan_ms)


async def execute(
    planned: PlannedQuery, deps: Deps, ctx: RequestContext, *, plan_ms: int = 0
) -> QueryResponse:
    """Stages 4 to 9 for a plan that is already checked."""
    watch = _Stopwatch(plan_ms)
    context = _MetaBuilder(planned, ctx, deps, watch)
    if planned.outcome is not None:
        return outcome_response(context.build(), planned.outcome)
    version = await deps.ctgov.version()
    context.version = version

    first_request = len(ctx.requests)
    resolved = await resolve_entities(planned, deps, ctx)
    watch.resolve_ms = watch.lap()
    if isinstance(resolved, Outcome):
        ctx.add_step("resolve_entity", resolved.message, watch.resolve_ms, first_request)
        return outcome_response(context.build(), resolved)
    ctx.add_step("resolve_entity", f"{len(resolved.entities)} entities", watch.resolve_ms, first_request)
    context.entities, context.assumptions = resolved.entities, resolved.assumptions
    context.warnings = resolved.warnings

    plan = lower_plan(planned, resolved, deps.catalog, version)
    limits = Limits.from_settings(deps.settings)
    xp = choose_strategy(plan, resolved.matched, limits, prefer_walk=deps.ctgov.is_throttled)
    if isinstance(xp, Outcome):
        return outcome_response(context.build(), xp, plan=plan)

    first_request = len(ctx.requests)
    result = await execute_plan(plan, xp, deps.ctgov, ctx)
    watch.fetch_ms = watch.lap()
    ctx.add_step("execute", f"{len(ctx.requests) - first_request} requests", watch.fetch_ms, first_request)
    shaped = shape(result, plan)
    context.strategy = shaped.steps
    context.assumptions = (*context.assumptions, *shaped.assumptions)
    if shaped.outcome is not None:
        return outcome_response(context.build(), shaped.outcome, plan=plan)

    shown = present(shaped, plan)
    overlap = await shared_trials(plan, resolved.matched, deps.ctgov, ctx)
    drawn = replace(shown.shaped, trials_in_several_series=overlap)
    response = build_response(context.build(), shown.plan, drawn, version, titles=ctx.titles)
    if isinstance(response, VisualizationResponse):
        _verify(response, ctx)
        response.meta.timing.total_ms = watch.timing().total_ms
    return response


def _verify(response: VisualizationResponse, ctx: RequestContext) -> None:
    violations = check_invariants(response, ctx)
    if violations:
        _log.error("invariant_violation", request_id=ctx.request_id, violations=violations)
        raise InvariantViolation(_INVARIANT_MESSAGE)


class _MetaBuilder:
    """The pieces of `meta` known so far, which grow as the stages complete."""

    def __init__(self, planned: PlannedQuery, ctx: RequestContext, deps: Deps, watch: _Stopwatch) -> None:
        self._planned, self._ctx, self._deps, self._watch = planned, ctx, deps, watch
        self.version: ApiVersion | None = None  # unknown until the first stage that needs the registry
        self.entities: tuple[EntityResolution, ...] = ()
        self.assumptions: tuple[str, ...] = ()
        self.warnings: tuple[Note, ...] = ()
        self.strategy: tuple[StrategyStep, ...] = ()

    def build(self) -> MetaContext:
        planned, ctx = self._planned, self._ctx
        now = self._deps.clock()
        filters: AppliedFilters = applied_filters(planned.plan)
        request = planned.request
        return MetaContext(
            request_id=ctx.request_id,
            generated_at=now,
            query=request.query if request is not None else None,
            request=request,
            filters=filters,
            plan=planned.plan,
            options=planned.options,
            planner=planned.info,
            timing=self._watch.timing(),
            cache=_NO_CACHE,
            adjustments=planned.adjustments,
            warnings=(*planned.warnings, *self.warnings),
            assumptions=self.assumptions,
            entities=self.entities,
            strategy=self.strategy,
            source=self._source(now),
            trace=tuple(ctx.trace) if planned.options.include_trace else None,
        )

    def _source(self, now: datetime) -> Source | None:
        version = self.version
        if version is None or not self._ctx.requests:
            return None
        return Source(
            api_version=version.api_version,
            data_timestamp=version.data_timestamp,
            retrieved_at=now,
            requests=list(self._ctx.requests),
        )
