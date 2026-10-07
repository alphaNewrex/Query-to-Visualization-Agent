"""`OpenAIPlanner` over the real SDK with `httpx2.MockTransport`: canned `/responses` bodies, no socket."""

import json
from collections.abc import Callable

import httpx2
import pytest

from ctviz.planning.planner import (
    OpenAIPlanner,
    PlannerMisconfigured,
    PlannerOutputError,
    PlannerRefused,
    PlannerResult,
    PlannerUnavailable,
    build_planners,
    sampling_params,
)
from ctviz.planning.prompt import PROMPT_VERSION
from ctviz.settings import Settings
from tests.unit.plan_samples import plan

pytestmark = pytest.mark.anyio

PLACEHOLDER_KEY = "sk-placeholder-0123456789"
BASE_URL = "https://gateway.example/v1"
PLAN_JSON = plan().model_dump_json()
USAGE = {
    "input_tokens": 100,
    "input_tokens_details": {"cached_tokens": 40},
    "output_tokens": 20,
    "output_tokens_details": {"reasoning_tokens": 7},
    "total_tokens": 120,
}


def response_body(content: list[dict[str, object]], *, status: str = "completed") -> dict[str, object]:
    output = [{"type": "message", "id": "msg_1", "status": status, "role": "assistant", "content": content}]
    return {
        "id": "resp_1", "object": "response", "created_at": 0, "status": status, "model": "snapshot-2026",
        "output": output if content else [], "usage": USAGE, "parallel_tool_calls": True,
        "tool_choice": "auto", "tools": [],
        **({"incomplete_details": {"reason": "max_output_tokens"}} if status == "incomplete" else {}),
    }  # fmt: skip


def answer(text: str = PLAN_JSON) -> dict[str, object]:
    return response_body([{"type": "output_text", "text": text, "annotations": []}])


class Provider:
    """Answers every `/responses` call with `respond` and keeps the requests it saw."""

    def __init__(self, respond: Callable[[httpx2.Request], httpx2.Response]) -> None:
        self.requests: list[httpx2.Request] = []
        self._respond = respond

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self._respond(request)

    def planner(self, model: str = "gpt-5.4-mini", effort: str | None = "low") -> OpenAIPlanner:
        settings = Settings(OPENAI_API_KEY=PLACEHOLDER_KEY, OPENAI_API_BASE=BASE_URL)
        client = httpx2.AsyncClient(transport=httpx2.MockTransport(self))
        return OpenAIPlanner(settings, model=model, effort=effort, http_client=client)

    @property
    def body(self) -> dict[str, object]:
        body: dict[str, object] = json.loads(self.requests[0].content)
        return body


def ok(
    body: dict[str, object] | None = None, status: int = 200
) -> Callable[[httpx2.Request], httpx2.Response]:
    return lambda _: httpx2.Response(status, json=body if body is not None else answer())


async def draft(provider: Provider, model: str = "gpt-5.4-mini") -> PlannerResult:
    planner = provider.planner(model)
    return await planner.draft(
        instructions="rules", messages=[{"role": "user", "content": "q"}], max_output_tokens=1500
    )


async def test_a_completed_answer_is_parsed_with_its_snapshot_usage_and_latency() -> None:
    provider = Provider(ok())
    result = await draft(provider)
    assert result.plan == plan()
    assert result.model == "snapshot-2026" and result.reasoning_effort == "low"
    assert (result.usage.input_tokens, result.usage.output_tokens) == (100, 20)
    assert (result.usage.reasoning_tokens, result.usage.cached_tokens) == (7, 40)
    assert result.latency_ms >= 0


async def test_the_request_is_stateless_strict_and_goes_to_the_configured_base_url() -> None:
    provider = Provider(ok())
    await draft(provider)
    [request] = provider.requests
    assert str(request.url) == f"{BASE_URL}/responses"
    assert request.headers["authorization"] == f"Bearer {PLACEHOLDER_KEY}"
    body = provider.body
    assert body["store"] is False and body["model"] == "gpt-5.4-mini" and body["instructions"] == "rules"
    assert body["max_output_tokens"] == 1500 and body["prompt_cache_key"] == f"ctviz-{PROMPT_VERSION}"
    assert body["reasoning"] == {"effort": "low"} and "temperature" not in body
    assert body["input"] == [{"role": "user", "content": "q"}]
    assert body["text"]["format"]["strict"] is True  # type: ignore[index]


@pytest.mark.parametrize(
    ("model", "effort", "reasoning", "temperature"),
    [
        ("gpt-5.4-mini", "low", {"effort": "low"}, None),
        ("gpt-5.4-mini", None, {"effort": "none"}, 0),
        ("gpt-5.1", "none", {"effort": "none"}, 0),
        ("gpt-5-mini", None, {"effort": "minimal"}, None),
        ("gpt-5", "minimal", {"effort": "minimal"}, None),
        ("gpt-4.1-mini", "low", None, 0),
        ("gpt-4o-mini", None, None, 0),
    ],
)
def test_sampling_parameters_follow_the_model_family(
    model: str, effort: str | None, reasoning: dict[str, str] | None, temperature: int | None
) -> None:
    params = sampling_params(model, effort)
    assert params.get("reasoning") == reasoning and params.get("temperature") == temperature


async def test_the_fallback_family_is_sent_a_temperature_and_no_effort() -> None:
    provider = Provider(ok())
    await draft(provider, "gpt-4.1-mini")
    assert provider.body["temperature"] == 0 and "reasoning" not in provider.body


@pytest.mark.parametrize(
    ("model", "effort", "reported"),
    [("gpt-5.4-mini", "low", "low"), ("gpt-5.4-mini", None, "none"), ("gpt-4.1-mini", "low", None)],
)
async def test_the_result_reports_the_effort_the_call_carried(
    model: str, effort: str | None, reported: str | None
) -> None:
    planner = Provider(ok()).planner(model, effort)
    result = await planner.draft(
        instructions="rules", messages=[{"role": "user", "content": "q"}], max_output_tokens=1500
    )
    assert result.reasoning_effort == reported


async def test_a_refusal_raises_refused() -> None:
    provider = Provider(ok(response_body([{"type": "refusal", "refusal": "I cannot help with that."}])))
    with pytest.raises(PlannerRefused):
        await draft(provider)


async def test_an_output_cut_off_mid_json_raises_output_error_without_the_partial_text() -> None:
    provider = Provider(ok(answer(PLAN_JSON[:60])))
    with pytest.raises(PlannerOutputError) as raised:
        await draft(provider)
    assert PLAN_JSON[:30] not in str(raised.value) and raised.value.__cause__ is None


async def test_incomplete_while_reasoning_raises_output_error_before_the_refusal_check() -> None:
    provider = Provider(ok(response_body([], status="incomplete")))
    with pytest.raises(PlannerOutputError, match="ran out of output tokens"):
        await draft(provider)


async def test_an_answer_with_no_plan_raises_output_error() -> None:
    provider = Provider(ok(response_body([])))
    with pytest.raises(PlannerOutputError, match="no plan"):
        await draft(provider)


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_throttling_and_server_errors_are_one_attempt_then_unavailable(status: int) -> None:
    provider = Provider(
        lambda _: httpx2.Response(status, json={"error": {"message": "echo sk-secret", "code": None}})
    )
    with pytest.raises(PlannerUnavailable) as raised:
        await draft(provider)
    assert len(provider.requests) == 1 and "sk-secret" not in str(raised.value)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 405])
async def test_client_errors_are_misconfiguration_whatever_the_error_code(status: int) -> None:
    provider = Provider(lambda _: httpx2.Response(status, json={"error": {"message": "x", "code": None}}))
    with pytest.raises(PlannerMisconfigured):
        await draft(provider)
    assert len(provider.requests) == 1


async def test_a_timeout_and_a_refused_connection_are_unavailable() -> None:
    def timeout(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow", request=request)

    def refused(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    for respond in (timeout, refused):
        provider = Provider(respond)
        with pytest.raises(PlannerUnavailable):
            await draft(provider)
        assert len(provider.requests) == 1


def model_list(*ids: str) -> dict[str, object]:
    return {
        "object": "list",
        "data": [{"id": name, "object": "model", "created": 0, "owned_by": "openai"} for name in ids],
    }


async def test_the_model_list_is_the_set_of_ids_the_key_can_use() -> None:
    provider = Provider(ok(model_list("gpt-5.4-mini", "gpt-4.1-mini", "gpt-4o")))

    listed = await provider.planner().list_models(timeout_s=2.0)

    assert listed == frozenset({"gpt-5.4-mini", "gpt-4.1-mini", "gpt-4o"})
    [request] = provider.requests
    assert (request.method, str(request.url)) == ("GET", f"{BASE_URL}/models")
    assert request.headers["authorization"] == f"Bearer {PLACEHOLDER_KEY}"


@pytest.mark.parametrize(
    ("status", "failure"),
    [(429, PlannerUnavailable), (503, PlannerUnavailable), (401, PlannerMisconfigured)],
)
async def test_a_model_list_that_fails_is_one_attempt_and_one_of_our_errors(
    status: int, failure: type[Exception]
) -> None:
    provider = Provider(lambda _: httpx2.Response(status, json={"error": {"message": "echo sk-secret"}}))

    with pytest.raises(failure) as raised:
        await provider.planner().list_models(timeout_s=2.0)

    assert len(provider.requests) == 1 and "sk-secret" not in str(raised.value)


@pytest.mark.parametrize("body", [{"object": "list"}, {"data": "nothing"}, {"data": None}])
async def test_an_answer_that_is_not_a_model_list_is_unavailable(body: dict[str, object]) -> None:
    provider = Provider(ok(body))

    with pytest.raises(PlannerUnavailable):
        await provider.planner().list_models(timeout_s=2.0)


async def test_a_model_list_that_times_out_is_unavailable() -> None:
    def timeout(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow", request=request)

    provider = Provider(timeout)

    with pytest.raises(PlannerUnavailable):
        await provider.planner().list_models(timeout_s=2.0)
    assert len(provider.requests) == 1


def test_without_a_key_there_is_no_planner_and_no_fallback() -> None:
    assert build_planners(Settings()) == (None, None)


def test_with_a_key_the_planner_and_the_fallback_are_built_from_the_settings() -> None:
    settings = Settings(OPENAI_API_KEY=PLACEHOLDER_KEY)
    planner, fallback = build_planners(settings)
    assert isinstance(planner, OpenAIPlanner) and isinstance(fallback, OpenAIPlanner)


def test_a_planner_cannot_be_built_without_a_key() -> None:
    with pytest.raises(PlannerMisconfigured):
        OpenAIPlanner(Settings(), model="gpt-5.4-mini", effort="low")
