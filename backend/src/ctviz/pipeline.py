"""The request path after validation: plan, resolve, choose a strategy, execute, shape, build, verify.

No HTTP here; `api/` calls `answer` and `execute`, and so do the examples recorder and the tests.

`execute` is also where the response cache sits (section 4.9 of the plan): everything after the plan is a
function of the plan, the options and the registry's data version, so an answer built once is served again
for as long as those stay the same.
"""

import hashlib
import json
import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Final

import anyio
import structlog
from pydantic_core import to_jsonable_python
from structlog.typing import FilteringBoundLogger

from ctviz import examples
from ctviz.applied import applied_filters
from ctviz.catalog.countries import CountryTable
from ctviz.catalog.fields import FieldSpec
from ctviz.contract.invariants import check_invariants
from ctviz.contract.request import QueryRequest
from ctviz.contract.response import (
    AppliedFilters,
    CacheInfo,
    ClarificationResponse,
    EntityResolution,
    Note,
    Outcome,
    QueryResponse,
    Source,
    StrategyStep,
    Timing,
    VisualizationResponse,
)
from ctviz.ctgov.cache import SingleFlightCache
from ctviz.ctgov.client import ApiVersion, CtGovClient
from ctviz.ctgov.context import RequestContext
from ctviz.ctgov.partition import SEQUENTIAL_PAGES
from ctviz.engine.execute import execute_plan
from ctviz.engine.fanout import fan_out_bill
from ctviz.engine.handoff import present
from ctviz.engine.lower import EnginePlan, lower_plan
from ctviz.engine.overlap import shared_trials
from ctviz.engine.resolve import EntityResolver, resolve_entities
from ctviz.engine.shape import shape
from ctviz.engine.strategy import SHORTENED_FOR_TIME, ExecutionPlan, Limits, choose_strategy
from ctviz.errors import InvariantViolation
from ctviz.planning.conversation import describe
from ctviz.planning.service import PlannedQuery, PlanService
from ctviz.progress import Progress, StageEvent, Status, Step
from ctviz.settings import Settings
from ctviz.viz import text
from ctviz.viz.meta import MetaContext, build_response, outcome_response

RESPONSE_TTL_S: Final = 24 * 60 * 60
DEFAULT_RESPONSE_CACHE_SIZE: Final = 256
# An answer carrying one of these warnings is not kept. The first says the registry was limiting requests;
# the other two say that its numbers moved while the answer was being read (a data refresh), so the
# answer describes no single version of the data.
_UNCACHEABLE_WARNINGS: Final = frozenset(
    {"upstream_throttled", "walk_count_mismatch", "counts_not_reconciled", SHORTENED_FOR_TIME}
)

# Seconds of the request deadline kept for grouping what a walk read and building the response.
_BUILD_RESERVE_S: Final = 6.0

_log: FilteringBoundLogger = structlog.get_logger()
_INVARIANT_MESSAGE = "The answer failed an internal consistency check and was withheld. Quote the request id."


@dataclass(frozen=True)
class BuiltResponse:
    """A finished response and when it was first built. It is never changed after it is stored."""

    response: QueryResponse
    built_at: datetime


def new_response_cache(
    size: int = DEFAULT_RESPONSE_CACHE_SIZE, clock: Callable[[], float] = time.monotonic
) -> SingleFlightCache[BuiltResponse]:
    return SingleFlightCache[BuiltResponse](size, RESPONSE_TTL_S, clock)


@dataclass(frozen=True)
class Deps:
    settings: Settings
    ctgov: CtGovClient
    plans: PlanService
    resolver: EntityResolver
    catalog: Mapping[str, FieldSpec]
    countries: CountryTable
    clock: Callable[[], datetime]
    responses: SingleFlightCache[BuiltResponse] = field(default_factory=new_response_cache)


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


def _emit(progress: Progress | None, step: Step, status: Status, summary: str, **detail: object) -> None:
    if progress is not None:
        progress(StageEvent(step, status, summary, detail))


async def answer(
    request: QueryRequest, deps: Deps, ctx: RequestContext, progress: Progress | None = None
) -> QueryResponse:
    """Stages 2 to 9: plan the question, then run the plan.

    `progress`, when given, is told as each stage starts and ends. The plan service checks the plan inside
    its own call, so the `check` events follow the `plan` ones and report what that check found.
    """
    started = time.perf_counter()
    first_request = len(ctx.requests)
    _emit(progress, "plan", "started", "Reading the question")
    planned = await deps.plans.produce(request, deps.clock().date(), ctx)
    plan_ms = round((time.perf_counter() - started) * 1000)
    ctx.add_step("plan", planned.plan.interpretation, plan_ms, first_request)
    _emit(
        progress,
        "plan",
        "done",
        planned.plan.interpretation,
        mode=planned.info.mode,
        attempts=planned.info.attempts,
        is_cached=planned.is_cached,
        is_follow_up=request.previous is not None,
    )
    _emit(progress, "check", "started", "Checking the plan against the question")
    _emit(progress, "check", "done", _check_summary(planned), **_check_detail(planned))
    return await execute(planned, deps, ctx, plan_ms=plan_ms, progress=progress)


def _check_summary(planned: PlannedQuery) -> str:
    parts = ["plan accepted" if planned.outcome is None else f"decided without data: {planned.outcome.kind}"]
    if planned.info.is_repaired:
        parts.append("after one repair turn")
    if planned.adjustments:
        count = len(planned.adjustments)
        parts.append(f"{count} adjustment{'s' if count != 1 else ''}")
    if planned.warnings:
        count = len(planned.warnings)
        parts.append(f"{count} warning{'s' if count != 1 else ''}")
    return ", ".join(parts)


def _check_detail(planned: PlannedQuery) -> dict[str, object]:
    return {
        "adjustments": [adjustment.code for adjustment in planned.adjustments],
        "warnings": [note.code for note in planned.warnings],
        "is_repaired": planned.info.is_repaired,
        "outcome": planned.outcome.kind if planned.outcome is not None else None,
    }


async def execute(
    planned: PlannedQuery,
    deps: Deps,
    ctx: RequestContext,
    *,
    plan_ms: int = 0,
    progress: Progress | None = None,
) -> QueryResponse:
    """Stages 4 to 9 for a plan that is already checked, or the answer already built for the same one.

    A clarification or unsupported answer decided before any data is fetched costs nothing to build and
    is not cached. `options.use_cache: false` neither reads nor writes the cache.
    """
    watch = _Stopwatch(plan_ms)
    if planned.outcome is not None:
        outcome = planned.outcome
        if outcome.kind == "conversation":
            directory = examples.examples_directory(deps.settings)
            outcome = replace(outcome, followups=tuple(examples.suggestions(directory)))
        return outcome_response(_MetaBuilder(planned, ctx, deps, watch).build(), outcome)
    version = await deps.ctgov.version()
    if not planned.options.use_cache:
        return await _run(planned, deps, ctx, watch, version, progress)
    built, is_shared = await deps.responses.get(
        _response_key(planned, version),
        lambda: _build(planned, deps, ctx, watch, version, progress),
        keep=lambda fresh: _is_cacheable(fresh.response),
    )
    if not is_shared:
        return built.response
    _emit(progress, "build", "started", "Looking for the same answer already built")
    _emit(progress, "build", "done", built.response.message, kind=built.response.kind, is_cached=True)
    return _as_cached(built, planned, deps, ctx, watch)


async def _build(
    planned: PlannedQuery,
    deps: Deps,
    ctx: RequestContext,
    watch: _Stopwatch,
    version: ApiVersion,
    progress: Progress | None,
) -> BuiltResponse:
    response = await _run(planned, deps, ctx, watch, version, progress)
    return BuiltResponse(response, built_at=response.meta.generated_at)


def _response_key(planned: PlannedQuery, version: ApiVersion) -> str:
    """A digest of everything the finished response is a function of.

    That is the canonical plan and how it was reached (the question and the planner's record, the
    adjustments and warnings of the checks: `meta` repeats all of them), the effective options, and the
    registry's version, so that a data refresh gives every question a new key.
    """
    document = {
        "plan": planned.plan,
        "request": planned.request,
        "options": planned.options,
        "planner": planned.info,
        "adjustments": planned.adjustments,
        "warnings": planned.warnings,
        "api_version": version.api_version,
        "data_timestamp": version.data_timestamp,
    }
    payload = json.dumps(to_jsonable_python(document), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _is_cacheable(response: QueryResponse) -> bool:
    """Whether to keep an answer: not one built while the registry limited requests or changed under it, nor
    one the time left on that request shaped (a `too_broad` clarification, an axis shortened for time)."""
    volatile = sorted(_UNCACHEABLE_WARNINGS & {note.code for note in response.meta.warnings})
    if isinstance(response, ClarificationResponse) and response.clarification.reason == "too_broad":
        volatile.append("too_broad")
    if volatile:
        _log.info("response_not_cached", warnings=volatile)
    return not volatile


def _as_cached(
    built: BuiltResponse, planned: PlannedQuery, deps: Deps, ctx: RequestContext, watch: _Stopwatch
) -> QueryResponse:
    """The stored answer, with what belongs to this request: its id, time, timing and cache flags.

    The request log and the trace stay those of the run that built the answer.
    """
    cache = CacheInfo(is_plan_cached=planned.is_cached, is_response_cached=True, cached_at=built.built_at)
    meta = built.response.meta.model_copy(
        update={
            "request_id": ctx.request_id,
            "generated_at": deps.clock(),
            "timing": watch.timing(),
            "cache": cache,
        }
    )
    _log.info("response_from_cache", cached_at=built.built_at.isoformat())
    return built.response.model_copy(update={"meta": meta})


async def _run(
    planned: PlannedQuery,
    deps: Deps,
    ctx: RequestContext,
    watch: _Stopwatch,
    version: ApiVersion,
    progress: Progress | None = None,
) -> QueryResponse:
    """Stages 4 to 9 against the registry, for the data version `version`."""
    context = _MetaBuilder(planned, ctx, deps, watch)
    context.version = version

    first_request = len(ctx.requests)
    _emit(progress, "resolve", "started", f"Looking up {_count_of(len(planned.plan.entities), 'name')}")
    resolved = await resolve_entities(planned, deps, ctx)
    watch.resolve_ms = watch.lap()
    if isinstance(resolved, Outcome):
        ctx.add_step("resolve_entity", resolved.message, watch.resolve_ms, first_request)
        _emit(progress, "resolve", "done", resolved.message, outcome=resolved.kind)
        return outcome_response(context.build(), resolved)
    ctx.add_step("resolve_entity", f"{len(resolved.entities)} entities", watch.resolve_ms, first_request)
    _emit(
        progress,
        "resolve",
        "done",
        _resolve_summary(resolved.entities),
        entities=[
            {"text": e.text, "kind": e.kind, "status": e.status, "trials_matched": e.trials_matched}
            for e in resolved.entities
        ],
    )
    context.entities, context.assumptions = resolved.entities, resolved.assumptions
    context.warnings = resolved.warnings
    if resolved.plan is not None:
        # The entities read as the kinds the registry's counts decided; the response states the plan it ran.
        planned = replace(
            planned, plan=resolved.plan, adjustments=(*planned.adjustments, *resolved.adjustments)
        )
        context.planned = planned

    plan = lower_plan(planned, resolved, deps.catalog, version)
    limits = Limits.from_settings(deps.settings, _walk_budget_s())
    _emit(progress, "strategy", "started", "Choosing how to fetch the trials")
    xp = choose_strategy(plan, resolved.matched, limits, prefer_walk=deps.ctgov.is_throttled)
    if isinstance(xp, Outcome):
        _emit(progress, "strategy", "done", xp.message, outcome=xp.kind)
        return outcome_response(context.build(), xp, plan=plan)
    _emit(
        progress,
        "strategy",
        "done",
        _strategy_summary(xp),
        strategies=[
            {"series": run.scope.label, "name": run.strategy, "trials": run.matched} for run in xp.runs
        ],
    )

    first_request = len(ctx.requests)
    expected = _expected_requests(plan, xp, limits)
    _emit(progress, "execute", "started", f"Fetching from ClinicalTrials.gov (about {expected} requests)")
    ctx.on_request = _request_counter(progress, first_request, expected)
    try:
        result = await execute_plan(plan, xp, deps.ctgov, ctx)
    finally:
        ctx.on_request = None
    fetched = len(ctx.requests) - first_request
    _emit(progress, "execute", "done", f"fetched {_count_of(fetched, 'request')}", fetched=fetched)
    watch.fetch_ms = watch.lap()
    ctx.add_step("execute", f"{len(ctx.requests) - first_request} requests", watch.fetch_ms, first_request)
    shaped = shape(result, plan)
    context.strategy = shaped.steps
    context.assumptions = (*context.assumptions, *shaped.assumptions)
    if shaped.outcome is not None:
        return outcome_response(context.build(), shaped.outcome, plan=plan)

    shown = present(shaped, plan)
    overlap = await shared_trials(plan, resolved.matched, deps.ctgov, ctx)
    drawn = replace(
        shown.shaped,
        trials_in_several_series=overlap.shared,
        warnings=(*shown.shaped.warnings, *overlap.warnings),
    )
    _emit(progress, "build", "started", "Building the chart")
    response = build_response(context.build(), shown.plan, drawn, version, titles=ctx.titles)
    if isinstance(response, VisualizationResponse):
        _verify(response, ctx)
        response.meta.timing.total_ms = watch.timing().total_ms
    _emit(progress, "build", "done", response.message, kind=response.kind)
    return response


def _count_of(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"


def _resolve_summary(entities: Sequence[EntityResolution]) -> str:
    if not entities:
        return "no names to look up"
    return "; ".join(f"{entity.text}: {entity.trials_matched:,} trials" for entity in entities)


def _strategy_summary(xp: ExecutionPlan) -> str:
    runs = xp.runs
    if len(runs) == 1:
        return runs[0].reason
    return "; ".join(f"{run.scope.label or 'all trials'}: {run.strategy.replace('_', ' ')}" for run in runs)


def _expected_requests(plan: EnginePlan, xp: ExecutionPlan, limits: Limits) -> int:
    """About how many registry requests the execution makes, from the strategy's own figures."""
    total = 0
    for run in xp.runs:
        if run.strategy == "none":
            continue
        if run.strategy == "count_fan_out":
            total += fan_out_bill(plan, xp.window) or 1
        elif run.strategy == "sorted_page":
            total += 1
        else:
            pages = math.ceil(run.matched / limits.one_page_max)
            total += 1 + pages * (2 if pages > SEQUENTIAL_PAGES else 1)
    return max(total, 1)


def _request_counter(
    progress: Progress | None, first_request: int, expected: int
) -> Callable[[int], None] | None:
    if progress is None:
        return None

    def count(logged: int) -> None:
        fetched = logged - first_request
        shown = max(expected, fetched)
        _emit(
            progress,
            "execute",
            "started",
            f"fetched {fetched} of {shown}",
            fetched=fetched,
            expected=shown,
        )

    return count


def _walk_budget_s() -> float:
    """Seconds a walk may take: what is left of the request's deadline, less the time to build the answer."""
    left = anyio.current_effective_deadline() - anyio.current_time()
    return max(0.0, left - _BUILD_RESERVE_S)


def _verify(response: VisualizationResponse, ctx: RequestContext) -> None:
    violations = check_invariants(response, ctx)
    if violations:
        _log.error("invariant_violation", request_id=ctx.request_id, violations=violations)
        raise InvariantViolation(_INVARIANT_MESSAGE)


class _MetaBuilder:
    """The pieces of `meta` known so far, which grow as the stages complete."""

    def __init__(self, planned: PlannedQuery, ctx: RequestContext, deps: Deps, watch: _Stopwatch) -> None:
        self.planned, self._ctx, self._deps, self._watch = planned, ctx, deps, watch
        self.version: ApiVersion | None = None  # unknown until the first stage that needs the registry
        self.entities: tuple[EntityResolution, ...] = ()
        self.assumptions: tuple[str, ...] = ()
        self.warnings: tuple[Note, ...] = ()
        self.strategy: tuple[StrategyStep, ...] = ()

    def build(self) -> MetaContext:
        planned, ctx = self.planned, self._ctx
        now = self._deps.clock()
        filters: AppliedFilters = applied_filters(planned.plan)
        request = planned.request
        previous = request.previous if request is not None else None
        talk = describe(previous.plan if previous is not None else None, planned.plan)
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
            cache=CacheInfo(is_plan_cached=planned.is_cached, is_response_cached=False, cached_at=None),
            adjustments=planned.adjustments,
            warnings=(*planned.warnings, *self.warnings, *self._throttling()),
            assumptions=(*self.assumptions, *text.carried_over_assumption(talk.scope)),
            conversation=talk.conversation,
            entities=self.entities,
            strategy=self.strategy,
            source=self._source(now),
            trace=tuple(ctx.trace) if planned.options.include_trace else None,
        )

    def _throttling(self) -> tuple[Note, ...]:
        """The registry limited requests lately (a 429 or 403 within ten minutes): the answer says so."""
        is_throttled = self.version is not None and self._deps.ctgov.is_throttled
        return (text.upstream_throttled(),) if is_throttled else ()

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
