"""The model call: the only module that imports `openai`.

One structured Responses API call per `draft`, with the failure states of section 4.5 turned into four
exceptions that the service's failure policy acts on. The SDK's own retry is off because it repeats a
timed-out call too, which would use most of the request deadline before the fallback model is tried.
"""

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final, Literal, Protocol, TypedDict, cast

import httpx2
import openai
from openai import AsyncOpenAI
from openai.types.responses import ParsedResponse, ResponseInputParam
from pydantic import ValidationError

from ctviz.contract.plan import QueryPlan
from ctviz.contract.response import Usage
from ctviz.planning.prompt import PROMPT_VERSION
from ctviz.settings import Settings

_FAMILIES_WITHOUT_EFFORT: Final = ("gpt-4o", "gpt-4.1")
_MISCONFIGURED_STATUSES: Final = frozenset({400, 401, 403, 404, 405})
_RETRYABLE_STATUS: Final = 429
_SERVER_ERROR: Final = 500


class Message(TypedDict):
    """One item of the Responses API `input`."""

    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class PlannerResult:
    plan: QueryPlan
    model: str  # the snapshot the alias resolved to, as the provider reported it
    reasoning_effort: str | None
    usage: Usage
    latency_ms: int


class PlannerError(Exception):
    """Base of the four ways a model call fails."""


class PlannerUnavailable(PlannerError):
    """Timeout, connection error, 429 or 5xx."""


class PlannerOutputError(PlannerError):
    """The output was cut off mid-JSON, or the call ended incomplete while the model was still reasoning."""


class PlannerRefused(PlannerError):
    """The model declined: a content item of type `refusal`."""


class PlannerMisconfigured(PlannerError):
    """400, 401, 403, 404 or 405: a wrong parameter, key, model id or base URL."""


class Planner(Protocol):
    async def draft(
        self, *, instructions: str, messages: Sequence[Message], max_output_tokens: int
    ) -> PlannerResult: ...


def effective_effort(model: str, effort: str | None) -> str | None:
    """The reasoning effort a call to `model` carries; None for the families that take none."""
    if model.startswith(_FAMILIES_WITHOUT_EFFORT):
        return None
    return effort or ("minimal" if model in ("gpt-5", "gpt-5-mini") else "none")


def sampling_params(model: str, effort: str | None) -> dict[str, object]:
    """The sampling parameters a model family accepts; a wrong one is an HTTP 400."""
    effective = effective_effort(model, effort)
    if effective is None:
        return {"temperature": 0}  # these families reject reasoning.effort
    params: dict[str, object] = {"reasoning": {"effort": effective}}
    if effective == "none":  # 5.1, 5.2 and 5.4 accept temperature only at effort none
        params["temperature"] = 0
    return params


class OpenAIPlanner:
    """Writes a plan with one model; `settings` gives the key, the base URL and the timeout."""

    def __init__(
        self,
        settings: Settings,
        *,
        model: str,
        effort: str | None,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        if settings.openai_api_key is None:
            raise PlannerMisconfigured("OPENAI_API_KEY is not set.")
        # The SDK reads OPENAI_BASE_URL and never the owner's OPENAI_API_BASE, so the URL is passed here.
        self._client = AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            base_url=settings.openai_api_base,
            timeout=settings.planner_timeout_s,
            max_retries=0,
            http_client=http_client,
        )
        self._model = model
        self._effort = effort

    async def draft(
        self, *, instructions: str, messages: Sequence[Message], max_output_tokens: int
    ) -> PlannerResult:
        started = time.monotonic()
        try:
            raw = await self._client.responses.with_raw_response.parse(
                model=self._model,
                instructions=instructions,
                input=cast(ResponseInputParam, list(messages)),
                text_format=QueryPlan,
                max_output_tokens=max_output_tokens,
                store=False,
                prompt_cache_key=f"ctviz-{PROMPT_VERSION}",
                **sampling_params(self._model, self._effort),  # type: ignore[arg-type]
            )
        except openai.APIStatusError as error:
            raise _classify(error) from None
        except openai.APIConnectionError:  # includes the SDK's timeout error
            raise PlannerUnavailable("The model did not answer in time or could not be reached.") from None
        try:
            response = raw.parse()
        except ValidationError:
            # The text of this error holds partial model output, so it is neither kept nor logged.
            raise PlannerOutputError("The model's output was cut off before the plan was complete.") from None
        plan = _plan_of(response)
        return PlannerResult(
            plan=plan,
            model=response.model,
            reasoning_effort=effective_effort(self._model, self._effort),
            usage=_usage_of(response),
            latency_ms=round((time.monotonic() - started) * 1000),
        )


def _classify(error: openai.APIStatusError) -> PlannerError:
    """A provider error as one of ours. The provider's own text may echo configuration, so it is not kept."""
    status = error.status_code
    if status == _RETRYABLE_STATUS or status >= _SERVER_ERROR:
        return PlannerUnavailable(f"The model service answered HTTP {status}.")
    if status in _MISCONFIGURED_STATUSES:
        return PlannerMisconfigured(f"The model service refused the request with HTTP {status}.")
    return PlannerMisconfigured(f"The model service answered an unexpected HTTP {status}.")


def _plan_of(response: ParsedResponse[QueryPlan]) -> QueryPlan:
    """The parsed plan, or the exception for why there is none; the order of the checks matters."""
    if response.status != "completed":
        # The token cap was reached while the model was still reasoning; output_parsed is None here too.
        raise PlannerOutputError("The model ran out of output tokens before it wrote a plan.")
    for item in response.output:
        if item.type == "message" and any(part.type == "refusal" for part in item.content):
            raise PlannerRefused("The model declined to answer.")
    if response.output_parsed is None:
        raise PlannerOutputError("The model returned no plan.")
    return response.output_parsed


def _usage_of(response: ParsedResponse[QueryPlan]) -> Usage:
    usage = response.usage
    if usage is None:
        return Usage(input_tokens=0, output_tokens=0, reasoning_tokens=0, cached_tokens=0)
    return Usage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        reasoning_tokens=usage.output_tokens_details.reasoning_tokens,
        cached_tokens=usage.input_tokens_details.cached_tokens,
    )


def build_planners(settings: Settings) -> tuple[Planner | None, Planner | None]:
    """The planner and the fallback the settings describe; both None when no key is configured."""
    if settings.openai_api_key is None:
        return None, None
    planner = OpenAIPlanner(settings, model=settings.planner_model, effort=settings.planner_effort)
    fallback_model = settings.planner_fallback_model
    if fallback_model is None:
        return planner, None
    return planner, OpenAIPlanner(settings, model=fallback_model, effort=settings.planner_effort)


@dataclass
class FakePlanner:
    """A planner for tests: answers with the scripted plans and raises the scripted exceptions, in order."""

    script: list[QueryPlan | PlannerError]
    model: str = "fake-model"
    calls: list[tuple[str, tuple[Message, ...], int]] = field(default_factory=list)

    async def draft(
        self, *, instructions: str, messages: Sequence[Message], max_output_tokens: int
    ) -> PlannerResult:
        self.calls.append((instructions, tuple(messages), max_output_tokens))
        if not self.script:
            raise AssertionError("The fake planner was called more often than it was scripted for.")
        step = self.script.pop(0)
        if isinstance(step, PlannerError):
            raise step
        usage = Usage(input_tokens=10, output_tokens=5, reasoning_tokens=0, cached_tokens=0)
        return PlannerResult(step, self.model, None, usage, latency_ms=0)
