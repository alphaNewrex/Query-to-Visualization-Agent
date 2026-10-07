"""`PlanService`: a request becomes a `PlannedQuery` by a model, by structured fields or by a supplied plan.

The model path makes at most three model calls: a first attempt, a retry or the fallback after a failure,
and the one repair turn. Every call is stateless.

A plan that passed its checks is remembered for a day (the plan cache), so a repeated question costs no
model call and, because model output is not repeatable, gets the same plan again.
"""

import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Final, Literal, Protocol

import structlog
from structlog.typing import FilteringBoundLogger

from ctviz.contract.plan import PlanIssue, QueryPlan, Unsupported
from ctviz.contract.request import AnalysisRequest, QueryRequest, RequestOptions
from ctviz.contract.response import Adjustment, Note, Outcome, PlannerInfo, Usage
from ctviz.ctgov.cache import SingleFlightCache
from ctviz.errors import FieldError, InvalidRequest, PlannerUnavailableError
from ctviz.planning.grounding import CountryTable, Mode
from ctviz.planning.outcomes import DECLINED_MESSAGE, unsupported
from ctviz.planning.planner import (
    Message,
    Planner,
    PlannerError,
    PlannerMisconfigured,
    PlannerOutputError,
    PlannerRefused,
    PlannerResult,
    PlannerUnavailable,
)
from ctviz.planning.prompt import PROMPT_VERSION, build_instructions, user_message
from ctviz.planning.structured import NO_FILTERS, plan_from_fields
from ctviz.planning.validate import PlanCheck, check_plan
from ctviz.settings import Settings

FIRST_TOKEN_CAP: Final = 1500
RETRY_TOKEN_CAP: Final = 3000
MAX_MODEL_CALLS: Final = 3
PLAN_TTL_S: Final = 24 * 60 * 60
_MISSING_MODEL: Final = (
    "No planning model is configured. Use options.planner 'structured', POST /v1/analyses or the examples."
)
_MISCONFIGURED: Final = (
    "The planning model is misconfigured; check OPENAI_API_KEY, OPENAI_API_BASE and the model names."
)

_log: FilteringBoundLogger = structlog.get_logger()


class PlanningContext(Protocol):
    """What planning needs of the request context; `RequestContext` provides it."""

    request_id: str


@dataclass(frozen=True)
class PlannedQuery:
    plan: QueryPlan  # canonical: merged, normalised, defaults made explicit
    request: QueryRequest | None  # None for POST /v1/analyses
    options: RequestOptions
    info: PlannerInfo  # becomes meta.planner
    adjustments: tuple[Adjustment, ...]
    warnings: tuple[Note, ...]
    outcome: Outcome | None  # a clarification or unsupported outcome decided before any data is fetched
    is_cached: bool = False  # the plan is another request's: from the plan cache, or from its model call


@dataclass(frozen=True)
class _Attempt:
    """One model-backed planning attempt: what it produced, and whether the plan may be remembered."""

    planned: PlannedQuery
    is_checked: bool  # the plan passed its checks, as written or after the repair turn


def _is_worth_keeping(attempt: _Attempt) -> bool:
    """A checked plan that answers the question is remembered; a refusal or a clarification is asked again.

    Declining is the model's least repeatable choice, so caching it would turn one unlucky reading into
    the answer for the rest of the day.
    """
    return attempt.is_checked and attempt.planned.plan.analysis.kind not in ("unsupported", "clarify")


@dataclass(frozen=True)
class _Draft:
    """One plan from one model."""

    planner: Planner
    result: PlannerResult
    is_fallback: bool


@dataclass
class _Calls:
    """The budget and the bill of one request's model calls."""

    count: int = 0
    usage: Usage | None = None

    def add(self, usage: Usage | None = None) -> None:
        self.count += 1
        if usage is None:
            return
        spent = self.usage
        self.usage = (
            usage
            if spent is None
            else Usage(
                input_tokens=spent.input_tokens + usage.input_tokens,
                output_tokens=spent.output_tokens + usage.output_tokens,
                reasoning_tokens=spent.reasoning_tokens + usage.reasoning_tokens,
                cached_tokens=spent.cached_tokens + usage.cached_tokens,
            )
        )


class PlanService:
    def __init__(
        self,
        planner: Planner | None,
        fallback: Planner | None,
        settings: Settings,
        *,
        countries: CountryTable | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._planner = planner
        self._fallback = fallback
        self._countries = countries
        self._instructions = build_instructions()
        self._model = settings.planner_model
        self._effort = settings.planner_effort
        self._plans = SingleFlightCache[_Attempt](settings.plan_cache_size, PLAN_TTL_S, clock)

    async def listed_models(self, *, timeout_s: float) -> frozenset[str] | None:
        """The models the key lists, or None when no model is configured; a `PlannerError` when unreadable."""
        if self._planner is None:
            return None
        return await self._planner.list_models(timeout_s=timeout_s)

    async def produce(self, request: QueryRequest, today: date, ctx: PlanningContext) -> PlannedQuery:
        self._reject_unknown_countries(request)
        if request.options.planner == "structured":
            return self._from_fields(request, today)
        planned = await self._from_cache_or_model(request, today)
        _log.info(
            "plan_produced",
            request_id=ctx.request_id,
            attempts=planned.info.attempts,
            model=planned.info.model,
            is_cached=planned.is_cached,
        )
        return planned

    async def _from_cache_or_model(self, request: QueryRequest, today: date) -> PlannedQuery:
        """The plan cache of section 4.9. `options.use_cache: false` neither reads nor writes it.

        Concurrent identical requests share one model call. Only a checked plan is kept: a failure, a
        refusal and a plan settled after the repair turn was spent are asked for again next time.
        """
        if not request.options.use_cache:
            return (await self._from_model(request, today)).planned
        attempt, is_shared = await self._plans.get(
            self._key(request, today),
            lambda: self._from_model(request, today),
            keep=_is_worth_keeping,
        )
        # The key holds the question and every field, so the plan fits any request that differs from the
        # first only in its options, which neither the model nor the checks read.
        return replace(attempt.planned, request=request, options=request.options, is_cached=is_shared)

    def _key(self, request: QueryRequest, today: date) -> tuple[str, ...]:
        """What the plan is a function of: the question and fields, the prompt, the model and the date.

        The request is dumped without its options and in canonical form, since validation has already
        collapsed whitespace, spelled phases and turned empty lists into null. The date is the model's
        "today", so a plan never outlives the day it was written for.
        """
        return (
            request.model_dump_json(exclude={"options"}),
            PROMPT_VERSION,
            self._model,
            self._effort or "",
            today.isoformat(),
        )

    def accept(self, body: AnalysisRequest, today: date) -> PlannedQuery:
        """A supplied plan: checked for shape and limits only, and a broken rule is a 422."""
        check = check_plan(body.plan, None, mode="supplied", countries=self._countries, today=today)
        _raise_if_blocked(check, "/plan")
        info = _info("supplied_plan", None, _Calls())
        return PlannedQuery(
            check.plan, None, body.options, info, check.adjustments, check.warnings, check.outcome
        )

    def _from_fields(self, request: QueryRequest, today: date) -> PlannedQuery:
        check = self._check(plan_from_fields(request), request, "structured", today)
        _raise_if_blocked(check, "")
        note = Note(
            code="question_not_interpreted", message="The question was not read; only the fields were used."
        )
        info = _info("structured", None, _Calls())
        return PlannedQuery(
            check.plan,
            request,
            request.options,
            info,
            check.adjustments,
            (note, *check.warnings),
            check.outcome,
        )

    async def _from_model(self, request: QueryRequest, today: date) -> _Attempt:
        if self._planner is None:
            raise PlannerUnavailableError(_MISSING_MODEL, reason="not_configured")
        calls = _Calls()
        messages: list[Message] = [{"role": "user", "content": user_message(request, today)}]
        try:
            draft = await self._first_draft(messages, calls)
        except PlannerRefused:
            calls.add()
            outcome = Outcome(kind="unsupported", reason="other", message=DECLINED_MESSAGE)
            declined = PlannedQuery(
                _declined_plan(), request, request.options, _info("llm", None, calls), (), (), outcome
            )
            return _Attempt(declined, is_checked=False)  # no plan was written, and a refusal may not recur
        check = self._check(draft.result.plan, request, "model", today)
        final = draft.result
        repaired: tuple[Adjustment, ...] = ()
        if check.blocking and calls.count < MAX_MODEL_CALLS:
            second = await self._repair(draft, messages, check.blocking, calls)
            if second is not None:
                first_issues, final = check.blocking, second
                check = self._check(second.plan, request, "model", today)
                persisting = {issue.code for issue in check.blocking}
                repaired = tuple(
                    Adjustment(code=issue.code, path=issue.path, message=issue.message, action="repaired")
                    for issue in first_issues
                    if issue.code not in persisting
                )
        is_unrepaired = bool(check.blocking)  # the repair turn is spent or could not run
        if is_unrepaired:
            check = self._check(final.plan, request, "model", today, after_repair=True)
        info = _info("llm", final, calls, is_repaired=bool(repaired), is_fallback=draft.is_fallback)
        planned = PlannedQuery(
            check.plan,
            request,
            request.options,
            info,
            (*repaired, *check.adjustments),
            check.warnings,
            check.outcome,
        )
        return _Attempt(planned, is_checked=not is_unrepaired)

    async def _first_draft(self, messages: Sequence[Message], calls: _Calls) -> _Draft:
        """The failure policy: a cut-off output is retried once with a doubled cap, then the fallback runs."""
        assert self._planner is not None
        queue: deque[tuple[Planner, int, bool]] = deque([(self._planner, FIRST_TOKEN_CAP, False)])
        if self._fallback is not None:
            queue.append((self._fallback, FIRST_TOKEN_CAP, True))
        while queue and calls.count < MAX_MODEL_CALLS:
            planner, cap, is_fallback = queue.popleft()
            try:
                result = await planner.draft(
                    instructions=self._instructions, messages=messages, max_output_tokens=cap
                )
            except PlannerMisconfigured:
                # No fallback: a bad key or model name must not hide behind a second model.
                raise PlannerUnavailableError(_MISCONFIGURED, reason="configuration") from None
            except PlannerOutputError:
                calls.add()
                if cap == FIRST_TOKEN_CAP and not is_fallback:
                    queue.appendleft((planner, RETRY_TOKEN_CAP, False))
            except PlannerUnavailable:
                calls.add()
            else:
                calls.add(result.usage)
                return _Draft(planner, result, is_fallback)
        raise PlannerUnavailableError("No planning model could write a plan. Try again.", reason="transient")

    async def _repair(
        self, draft: _Draft, messages: Sequence[Message], issues: Sequence[PlanIssue], calls: _Calls
    ) -> PlannerResult | None:
        """The one stateless repair turn: the question, the previous plan and the issue list."""
        turn: list[Message] = [
            *messages,
            {"role": "assistant", "content": draft.result.plan.model_dump_json()},
            {"role": "user", "content": _issue_message(issues)},
        ]
        try:
            result = await draft.planner.draft(
                instructions=self._instructions, messages=turn, max_output_tokens=FIRST_TOKEN_CAP
            )
        except PlannerError:
            calls.add()
            return None
        calls.add(result.usage)
        return result

    def _check(
        self, plan: QueryPlan, request: QueryRequest, mode: Mode, today: date, *, after_repair: bool = False
    ) -> PlanCheck:
        return check_plan(
            plan, request, mode=mode, countries=self._countries, today=today, after_repair=after_repair
        )

    def _reject_unknown_countries(self, request: QueryRequest) -> None:
        """A country the client wrote in a field must resolve, or the request is a 422 with close names."""
        if self._countries is None:
            return
        errors = []
        for index, name in enumerate(request.country or []):
            if self._countries.resolve(name) is None:
                close = ", ".join(self._countries.suggest(name)[:3])
                hint = f" Close names: {close}." if close else ""
                message = f"'{name}' is not a country name the registry uses.{hint}"
                errors.append(FieldError(f"/country/{index}", "unknown_country", message))
        if errors:
            raise InvalidRequest("A country in the request is not one the registry uses.", errors)


def _raise_if_blocked(check: PlanCheck, prefix: str) -> None:
    if check.blocking:
        errors = [FieldError(f"{prefix}{issue.path}", issue.code, issue.message) for issue in check.blocking]
        raise InvalidRequest("The plan breaks one or more rules.", errors)


def _info(
    mode: Literal["llm", "structured", "supplied_plan"],
    result: PlannerResult | None,
    calls: _Calls,
    *,
    is_repaired: bool = False,
    is_fallback: bool = False,
) -> PlannerInfo:
    return PlannerInfo(
        mode=mode,
        model=result.model if result else None,
        reasoning_effort=result.reasoning_effort if result else None,
        prompt_version=PROMPT_VERSION if mode == "llm" else None,
        attempts=calls.count,
        is_repaired=is_repaired,
        is_fallback=is_fallback,
        usage=calls.usage,
    )


def _issue_message(issues: Sequence[PlanIssue]) -> str:
    lines = [
        f"- {issue.code} at {issue.path}: {issue.message}"
        + (f" Allowed: {', '.join(issue.allowed)}." if issue.allowed else "")
        for issue in issues
    ]
    return "The plan above has problems that the service cannot fix. Return a corrected plan.\n" + "\n".join(
        lines
    )


def _declined_plan() -> QueryPlan:
    """The plan of a question the model declined; replaying it gives the same outcome."""
    outcome = unsupported("other")
    return QueryPlan(
        interpretation=outcome.message,
        entities=[],
        filters=NO_FILTERS,
        analysis=Unsupported(kind="unsupported", category="other", reason=outcome.message),
        chart_preference=None,
        unapplied=[],
    )
