"""GET /readyz: the registry must answer; the planner's model list is reported and never fatal.

The application runs with a `FakePlanner` that lists models, and a registry that is a `httpx2.MockTransport`.
"""

from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

import anyio
import httpx2
import pytest
import stamina
from fastapi import FastAPI

from ctviz.api import readiness
from ctviz.api.app import create_app
from ctviz.errors import ErrorCode
from ctviz.planning.planner import (
    FakePlanner,
    PlannerMisconfigured,
    PlannerUnavailable,
)
from ctviz.settings import Settings
from tests.client.conftest import VERSION_BODY

pytestmark = pytest.mark.anyio

PLACEHOLDER_KEY = "sk-placeholder-0123456789-abcdefghijklmnopqrstuvwxyz-9876543210"
CONFIGURED = ["gpt-5.4-mini", "gpt-4.1-mini"]  # the defaults of `Settings`: the planner, then its fallback

Handler = Callable[[httpx2.Request], httpx2.Response]


class Registry:
    """Answers `/version`; keeps what it was asked."""

    def __init__(self, respond: Handler | None = None) -> None:
        self.respond: Handler = respond or (lambda request: httpx2.Response(200, json=VERSION_BODY))
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.respond(request)


class StalledPlanner(FakePlanner):
    """A provider that never answers the model list."""

    async def list_models(self, *, timeout_s: float) -> frozenset[str]:
        await anyio.sleep_forever()
        return frozenset()


@pytest.fixture(autouse=True)
def no_backoff() -> Iterator[None]:
    """Three attempts, as in production, without the waiting between them."""
    with stamina.set_testing(True, attempts=3):
        yield


@asynccontextmanager
async def serving(
    registry: Registry | None = None, planner: FakePlanner | None = None, **settings: Any
) -> AsyncIterator[httpx2.AsyncClient]:
    app: FastAPI = create_app(
        Settings(**settings), planner=planner, transport=httpx2.MockTransport(registry or Registry())
    )
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as http:
            yield http


def listing(*models: str) -> FakePlanner:
    return FakePlanner([], listed=frozenset(models))


def planner_part(models_check: str, *, missing: Sequence[str] = (), available: bool = True) -> dict[str, Any]:
    return {
        "is_available": available,
        "models": CONFIGURED if available else [],
        "models_check": models_check,
        "missing_models": list(missing),
    }


REGISTRY_UP = {
    "is_reachable": True,
    "api_version": VERSION_BODY["apiVersion"],
    "data_timestamp": VERSION_BODY["dataTimestamp"],
    "reason": None,
}


async def test_it_is_ready_when_the_registry_answers_and_the_key_lists_the_configured_models() -> None:
    # The key lists more than the service uses: the check is a subset, not an equality.
    async with serving(planner=listing(*CONFIGURED, "gpt-4o", "gpt-5.4")) as http:
        reply = await http.get("/readyz")

    assert reply.status_code == 200
    assert reply.json() == {
        "status": "ready",
        "configuration": {"is_loaded": True},
        "registry": REGISTRY_UP,
        "planner": planner_part("ok"),
    }


async def test_a_configured_model_the_key_does_not_list_is_reported_and_is_not_fatal() -> None:
    async with serving(planner=listing("gpt-5.4-mini", "gpt-4o")) as http:
        reply = await http.get("/readyz")

    assert reply.status_code == 200
    assert reply.json()["status"] == "ready"
    assert reply.json()["planner"] == planner_part("missing", missing=["gpt-4.1-mini"])


async def test_a_model_list_that_cannot_be_read_is_reported_and_is_not_fatal() -> None:
    for failure in (PlannerUnavailable("down"), PlannerMisconfigured("bad key"), RuntimeError("boom")):
        async with serving(planner=FakePlanner([], listed=failure)) as http:
            reply = await http.get("/readyz")

        assert reply.status_code == 200
        assert reply.json()["status"] == "ready"
        assert reply.json()["planner"] == planner_part("unavailable")


async def test_a_model_list_that_takes_too_long_is_given_up_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(readiness, "MODELS_TIMEOUT_S", 0.05)

    async with serving(planner=StalledPlanner([])) as http:
        with anyio.fail_after(2):
            reply = await http.get("/readyz")

    assert reply.status_code == 200 and reply.json()["planner"] == planner_part("unavailable")


async def test_with_no_model_configured_it_is_ready_and_says_so() -> None:
    async with serving() as http:
        reply = await http.get("/readyz")

    assert reply.status_code == 200
    assert reply.json()["planner"] == planner_part("skipped", available=False)
    assert reply.json()["registry"] == REGISTRY_UP


async def test_an_unreachable_registry_makes_it_unready_and_the_rest_is_still_reported() -> None:
    registry = Registry(lambda request: httpx2.Response(503, text="maintenance"))

    async with serving(registry, listing(*CONFIGURED)) as http:
        reply = await http.get("/readyz")

    assert reply.status_code == 503
    assert reply.json()["status"] == "not_ready"
    assert reply.json()["registry"] == {
        "is_reachable": False,
        "api_version": None,
        "data_timestamp": None,
        "reason": ErrorCode.UPSTREAM_UNAVAILABLE.value,
    }
    assert reply.json()["planner"] == planner_part("ok")
    assert len(registry.requests) == 3  # the client's three attempts


async def test_a_registry_that_does_not_answer_in_time_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(readiness, "REGISTRY_TIMEOUT_S", 0.05)

    async def stalled(request: httpx2.Request) -> httpx2.Response:
        await anyio.sleep_forever()
        return httpx2.Response(200)

    app = create_app(Settings(), transport=httpx2.MockTransport(stalled))
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as http:
            with anyio.fail_after(2):
                reply = await http.get("/readyz")

    assert reply.status_code == 503
    assert reply.json()["registry"]["reason"] == ErrorCode.UPSTREAM_TIMEOUT.value


async def test_the_registry_version_is_remembered_so_that_polling_costs_one_request() -> None:
    registry = Registry()

    async with serving(registry) as http:
        await http.get("/readyz")
        await http.get("/readyz")

    assert len(registry.requests) == 1


async def test_the_report_never_holds_the_key() -> None:
    async with serving(planner=listing(*CONFIGURED), OPENAI_API_KEY=PLACEHOLDER_KEY) as http:
        reply = await http.get("/readyz")

    assert PLACEHOLDER_KEY not in reply.text


async def test_the_route_is_documented_with_its_503() -> None:
    async with serving() as http:
        document = (await http.get("/openapi.json")).json()

    operation = document["paths"]["/readyz"]["get"]
    assert set(operation["responses"]) >= {"200", "503"}
