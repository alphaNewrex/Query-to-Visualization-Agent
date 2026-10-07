"""The response cache (section 4.9), through the application: a scripted registry and a scripted planner.

A question is asked twice and the second answer must come from the cache: the same drawing, the fields that
belong to one request made fresh, and no model call and no registry request behind it.
"""

import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import anyio
import httpx2
import pytest
import stamina
from fastapi import FastAPI
from pydantic import TypeAdapter

from ctviz.api.app import create_app
from ctviz.contract.response import Note, QueryResponse
from ctviz.pipeline import _is_cacheable
from ctviz.planning.planner import FakePlanner, PlannerUnavailable
from ctviz.settings import Settings
from tests.client.conftest import VERSION_BODY
from tests.unit.plan_samples import entity, plan

pytestmark = pytest.mark.anyio

FIXTURES = Path(__file__).parent.parent / "fixtures"
REGISTRY_SIZE = 600_000
NEW_TIMESTAMP = "2026-10-07T09:00:05"
PLAN = plan(entities=[entity("condition", "Duchenne muscular dystrophy")])
QUESTION: dict[str, Any] = {"query": "How are Duchenne muscular dystrophy trials distributed across phases?"}
STRUCTURED: dict[str, Any] = {
    "query": "Duchenne trials by phase",
    "condition": "Duchenne muscular dystrophy",
    "group_by": ["phase"],
    "options": {"planner": "structured"},
}
RESPONSES: TypeAdapter[Any] = TypeAdapter(QueryResponse)
VOLATILE = ("request_id", "generated_at", "timing", "cache")


class Registry:
    """A registry in which every search matches the same 499 records; it keeps what it is asked."""

    def __init__(self, *, delay_s: float = 0.0) -> None:
        self.records: list[dict[str, Any]] = json.loads(
            (FIXTURES / "duchenne_records.json").read_text("utf-8")
        )
        self.delay_s = delay_s
        self.data_timestamp = VERSION_BODY["dataTimestamp"]
        self.is_down = False
        self.reported_total: int | None = None  # what `totalCount` says instead of the number of records
        self.throttled_once = False
        self.searches: list[str] = []

    async def __call__(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/version"):
            return httpx2.Response(200, json={**VERSION_BODY, "dataTimestamp": self.data_timestamp})
        self.searches.append(str(request.url))
        await anyio.sleep(self.delay_s)
        if self.is_down:
            return httpx2.Response(500, text="down")
        if self.throttled_once:
            self.throttled_once = False
            return httpx2.Response(429, text="slow down", headers={"Retry-After": "0"})
        params = request.url.params
        is_search = any(name.startswith("query.") or name == "filter.advanced" for name in params)
        size = int(params.get("pageSize", "10"))
        total = len(self.records) if self.reported_total is None else self.reported_total
        body = {
            "studies": self.records[:size] if is_search else [],
            "totalCount": total if is_search else REGISTRY_SIZE,
        }
        return httpx2.Response(200, json=body)


@pytest.fixture(autouse=True)
def no_backoff() -> Iterator[None]:
    """Three attempts, as in production, without the waiting between them."""
    with stamina.set_testing(True, attempts=3):
        yield


@asynccontextmanager
async def serving(app: FastAPI, *, raise_app_exceptions: bool = True) -> AsyncIterator[httpx2.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions)
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as http:
            yield http


def application(registry: Registry, planner: FakePlanner | None = None) -> FastAPI:
    return create_app(Settings(), planner=planner, transport=httpx2.MockTransport(registry))


def stable(document: dict[str, Any]) -> dict[str, Any]:
    """The answer without the fields that belong to one request."""
    meta = {key: value for key, value in document["meta"].items() if key not in VOLATILE}
    return {**document, "meta": meta}


def moment(text: str) -> datetime:
    return datetime.fromisoformat(text)


def cache_of(reply: httpx2.Response) -> dict[str, Any]:
    cache: dict[str, Any] = reply.json()["meta"]["cache"]
    return cache


FRESH = {"is_plan_cached": False, "is_response_cached": False, "cached_at": None}


# --- a hit -------------------------------------------------------------------------------------------


async def test_a_repeated_question_is_answered_from_the_cache_with_fresh_volatile_fields() -> None:
    planner, registry = FakePlanner([PLAN]), Registry(delay_s=0.02)
    async with serving(application(registry, planner)) as http:
        first = await http.post("/v1/query", json=QUESTION)
        searches_of_the_first = len(registry.searches)
        second = await http.post("/v1/query", json=QUESTION)
    built, served = first.json(), second.json()

    assert (first.status_code, second.status_code) == (200, 200)
    RESPONSES.validate_python(built)
    RESPONSES.validate_python(served)
    assert built["kind"] == "visualization" and searches_of_the_first > 0
    assert len(planner.calls) == 1 and len(registry.searches) == searches_of_the_first
    assert stable(served) == stable(built)
    assert cache_of(first) == FRESH
    assert cache_of(second) == {
        "is_plan_cached": True,
        "is_response_cached": True,
        "cached_at": built["meta"]["generated_at"],
    }
    assert served["meta"]["request_id"] == second.headers["X-Request-ID"] != built["meta"]["request_id"]
    assert moment(served["meta"]["generated_at"]) > moment(built["meta"]["generated_at"])
    timing = served["meta"]["timing"]
    assert (timing["resolve_ms"], timing["fetch_ms"], timing["build_ms"]) == (0, 0, 0)
    assert (
        built["meta"]["timing"]["fetch_ms"] >= 15 and timing["total_ms"] < built["meta"]["timing"]["total_ms"]
    )


async def test_the_answer_keeps_the_request_log_and_the_trace_of_the_run_that_built_it() -> None:
    async with serving(application(Registry(), FakePlanner([PLAN]))) as http:
        built = (await http.post("/v1/query", json=QUESTION)).json()
        served = (await http.post("/v1/query", json=QUESTION)).json()

    assert served["meta"]["source"] == built["meta"]["source"]
    assert served["meta"]["debug"] == built["meta"]["debug"]
    assert all(not item["is_cached"] for item in served["meta"]["source"]["requests"][:1])


async def test_a_structured_request_is_answered_from_the_cache_too() -> None:
    registry = Registry()
    async with serving(application(registry)) as http:
        first = await http.post("/v1/query", json=STRUCTURED)
        searches_of_the_first = len(registry.searches)
        second = await http.post("/v1/query", json=STRUCTURED)

    assert cache_of(first) == FRESH
    assert cache_of(second)["is_response_cached"] is True and cache_of(second)["is_plan_cached"] is False
    assert len(registry.searches) == searches_of_the_first
    assert stable(second.json()) == stable(first.json())


async def test_another_question_with_the_same_plan_is_not_given_the_first_ones_answer() -> None:
    other = {"query": "Which phases do Duchenne muscular dystrophy trials fall into?"}
    planner = FakePlanner([PLAN, PLAN])
    async with serving(application(Registry(), planner)) as http:
        first = await http.post("/v1/query", json=QUESTION)
        second = await http.post("/v1/query", json=other)

    assert cache_of(second) == FRESH and len(planner.calls) == 2
    assert second.json()["meta"]["query"] == other["query"] != first.json()["meta"]["query"]


async def test_a_replayed_plan_has_its_own_answer_and_is_cached_on_its_own() -> None:
    registry = Registry()
    async with serving(application(registry, FakePlanner([PLAN]))) as http:
        asked = (await http.post("/v1/query", json=QUESTION)).json()
        replay = {"plan": asked["meta"]["plan"], "options": asked["meta"]["options"]}
        first = await http.post("/v1/analyses", json=replay)
        second = await http.post("/v1/analyses", json=replay)
        bypassing = await http.post(
            "/v1/analyses", json={**replay, "options": {**replay["options"], "use_cache": False}}
        )

    assert cache_of(first) == FRESH
    assert cache_of(second)["is_response_cached"] is True
    assert cache_of(bypassing) == FRESH
    for reply in (first, second):
        assert reply.json()["meta"]["query"] is None
        assert reply.json()["meta"]["planner"]["mode"] == "supplied_plan"
        assert reply.json()["visualization"] == asked["visualization"]


async def test_a_clarification_is_planned_again_and_asks_the_registry_nothing() -> None:
    asking = plan(
        entities=[], analysis={"kind": "clarify", "reason": "missing_entity", "missing": ["drug_name"]}
    )
    planner, registry = FakePlanner([asking, asking]), Registry()
    question = {"query": "How has the number of trials for this drug changed?"}
    async with serving(application(registry, planner)) as http:
        first = await http.post("/v1/query", json=question)
        second = await http.post("/v1/query", json=question)

    assert [r.json()["kind"] for r in (first, second)] == ["clarification", "clarification"]
    assert cache_of(first) == FRESH
    assert cache_of(second) == FRESH
    assert len(planner.calls) == 2 and registry.searches == []


# --- a new data version --------------------------------------------------------------------------------


async def test_a_new_data_timestamp_is_a_miss_and_the_new_version_is_cached_in_turn() -> None:
    planner, registry = FakePlanner([PLAN]), Registry()
    app = application(registry, planner)
    async with serving(app) as http:
        first = await http.post("/v1/query", json=QUESTION)
        searches_of_the_first = len(registry.searches)
        registry.data_timestamp = NEW_TIMESTAMP
        app.state.deps.ctgov._version_expires = 0.0  # the registry's version is read again, not remembered
        second = await http.post("/v1/query", json=QUESTION)
        third = await http.post("/v1/query", json=QUESTION)

    assert first.json()["meta"]["source"]["data_timestamp"] == VERSION_BODY["dataTimestamp"]
    assert cache_of(second) == {"is_plan_cached": True, "is_response_cached": False, "cached_at": None}
    assert second.json()["meta"]["source"]["data_timestamp"] == NEW_TIMESTAMP
    assert len(registry.searches) > searches_of_the_first
    assert cache_of(third)["cached_at"] == second.json()["meta"]["generated_at"]
    assert len(planner.calls) == 1


# --- use_cache: false -----------------------------------------------------------------------------------


async def test_use_cache_false_bypasses_both_caches_and_leaves_the_registry_call_cache_alone() -> None:
    planner, registry = FakePlanner([PLAN, PLAN, PLAN]), Registry()
    bypassing = {**QUESTION, "options": {"use_cache": False}}
    async with serving(application(registry, planner)) as http:
        first = await http.post("/v1/query", json=bypassing)
        searches_of_the_first = len(registry.searches)
        second = await http.post("/v1/query", json=bypassing)
        ordinary = await http.post("/v1/query", json=QUESTION)
        again = await http.post("/v1/query", json=QUESTION)

    assert [cache_of(reply) for reply in (first, second, ordinary)] == [FRESH, FRESH, FRESH]
    assert len(planner.calls) == 3  # the bypassing requests wrote no plan for the ordinary one to use
    assert all(item["is_cached"] for item in second.json()["meta"]["source"]["requests"])
    assert len(registry.searches) == searches_of_the_first  # the registry-call cache still answered
    assert cache_of(again)["is_response_cached"] is True and cache_of(again)["is_plan_cached"] is True


# --- what is not cached ----------------------------------------------------------------------------------


async def test_an_error_is_not_cached_and_the_plan_behind_it_is() -> None:
    planner, registry = FakePlanner([PLAN]), Registry()
    registry.is_down = True
    # The application's own error reply is read, not an exception raised into the test: whichever code the
    # failure is reported under, this test is about what the cache does with it.
    async with serving(application(registry, planner), raise_app_exceptions=False) as http:
        failed = await http.post("/v1/query", json=QUESTION)
        registry.is_down = False
        retried = await http.post("/v1/query", json=QUESTION)

    assert failed.status_code >= 500 and "error" in failed.json()
    assert retried.status_code == 200 and retried.json()["kind"] == "visualization"
    assert cache_of(retried) == {"is_plan_cached": True, "is_response_cached": False, "cached_at": None}
    assert len(planner.calls) == 1


async def test_a_failed_planning_attempt_is_not_cached() -> None:
    planner = FakePlanner([PlannerUnavailable("down"), PLAN])
    async with serving(application(Registry(), planner)) as http:
        failed = await http.post("/v1/query", json=QUESTION)
        retried = await http.post("/v1/query", json=QUESTION)

    assert failed.status_code == 503 and failed.json()["error"]["details"] == {"reason": "transient"}
    assert retried.status_code == 200 and cache_of(retried) == FRESH


async def test_an_answer_built_while_the_registry_was_limiting_requests_says_so_and_is_not_cached() -> None:
    planner, registry = FakePlanner([PLAN]), Registry()
    registry.throttled_once = True
    async with serving(application(registry, planner)) as http:
        first = await http.post("/v1/query", json=QUESTION)
        second = await http.post("/v1/query", json=QUESTION)

    for reply in (first, second):
        assert "upstream_throttled" in [w["code"] for w in reply.json()["meta"]["warnings"]]
        assert cache_of(reply)["is_response_cached"] is False
    assert len(planner.calls) == 1


async def test_an_answer_whose_walk_read_other_trials_than_the_registry_counted_is_not_cached() -> None:
    registry = Registry()
    registry.reported_total = len(registry.records) + 1  # a data refresh during the walk looks like this
    async with serving(application(registry, FakePlanner([PLAN]))) as http:
        first = await http.post("/v1/query", json=QUESTION)
        second = await http.post("/v1/query", json=QUESTION)

    for reply in (first, second):
        assert "walk_count_mismatch" in [w["code"] for w in reply.json()["meta"]["warnings"]]
        assert cache_of(reply)["is_response_cached"] is False


@pytest.mark.parametrize(
    ("code", "is_kept"),
    [
        ("upstream_throttled", False),
        ("walk_count_mismatch", False),
        ("counts_not_reconciled", False),
        ("recent_subset", True),
    ],
)
async def test_which_warnings_keep_an_answer_out_of_the_cache(code: str, is_kept: bool) -> None:
    async with serving(application(Registry(), FakePlanner([PLAN]))) as http:
        document = (await http.post("/v1/query", json=QUESTION)).json()
    response = RESPONSES.validate_python(document)
    response.meta.warnings.append(Note(code=code, message="A caveat."))

    assert _is_cacheable(response) is is_kept


# --- identical requests in flight -----------------------------------------------------------------------


async def test_two_identical_requests_at_the_same_time_make_one_run() -> None:
    alone = Registry(delay_s=0.05)  # how many registry requests one run makes
    async with serving(application(alone, FakePlanner([PLAN]))) as http:
        await http.post("/v1/query", json=QUESTION)
    searches_of_one_run = len(alone.searches)

    planner, registry = FakePlanner([PLAN]), Registry(delay_s=0.05)
    replies: list[httpx2.Response] = []

    async def ask(http: httpx2.AsyncClient) -> None:
        replies.append(await http.post("/v1/query", json=QUESTION))

    async with serving(application(registry, planner)) as http, anyio.create_task_group() as group:
        group.start_soon(ask, http)
        group.start_soon(ask, http)

    first, second = (reply.json() for reply in replies)
    assert [reply.status_code for reply in replies] == [200, 200]
    assert len(planner.calls) == 1 and len(registry.searches) == searches_of_one_run
    assert stable(first) == stable(second)
    assert sorted(
        cache["is_response_cached"] for cache in (first["meta"]["cache"], second["meta"]["cache"])
    ) == [
        False,
        True,
    ]
    assert first["meta"]["request_id"] != second["meta"]["request_id"]
    follower = first if first["meta"]["cache"]["is_response_cached"] else second
    assert follower["meta"]["timing"]["total_ms"] >= 40  # it waited for the run in flight
    assert follower["meta"]["cache"]["cached_at"] is not None


async def test_requests_that_differ_in_their_options_do_not_share_a_run() -> None:
    planner, registry = FakePlanner([PLAN]), Registry(delay_s=0.02)
    traceless = {**QUESTION, "options": {"include_trace": False}}
    replies: list[httpx2.Response] = []

    async def ask(http: httpx2.AsyncClient, body: dict[str, Any]) -> None:
        replies.append(await http.post("/v1/query", json=body))

    async with serving(application(registry, planner)) as http, anyio.create_task_group() as group:
        group.start_soon(ask, http, QUESTION)
        group.start_soon(ask, http, traceless)

    assert len(planner.calls) == 1  # the plan does not depend on the options, so it is shared
    assert sorted(reply.json()["meta"]["debug"] is None for reply in replies) == [False, True]
    assert [cache_of(reply)["is_response_cached"] for reply in replies] == [False, False]
