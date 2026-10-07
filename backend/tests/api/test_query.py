"""POST /v1/query and the reference routes, over a scripted registry and a scripted planner."""

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi import FastAPI
from pydantic import TypeAdapter

from ctviz.api.app import create_app
from ctviz.contract.response import QueryResponse
from ctviz.planning.planner import FakePlanner
from ctviz.settings import Settings
from tests.client.conftest import VERSION_BODY
from tests.unit.plan_samples import BASE, plan

pytestmark = pytest.mark.anyio

FIXTURES = Path(__file__).parent.parent / "fixtures"
DUCHENNE_PHASES = {
    "Early Phase 1": 10,
    "Phase 1": 49,
    "Phase 1/Phase 2": 47,
    "Phase 2": 88,
    "Phase 2/Phase 3": 11,
    "Phase 3": 50,
    "Phase 4": 11,
    "Not Applicable": 91,
    "No phase listed": 142,
}
REGISTRY_SIZE = 600_000
RESPONSES: TypeAdapter[Any] = TypeAdapter(QueryResponse)


def registry(records: list[dict[str, Any]], seen: list[str]) -> httpx2.MockTransport:
    """A registry in which every search matches the same records and an unfiltered one matches everything."""

    def handle(request: httpx2.Request) -> httpx2.Response:
        seen.append(str(request.url))
        if request.url.path.endswith("/version"):
            return httpx2.Response(200, json=VERSION_BODY)
        params = request.url.params
        is_search = any(name.startswith("query.") or name == "filter.advanced" for name in params)
        size = int(params.get("pageSize", "10"))
        body: dict[str, Any] = {
            "studies": records[:size] if is_search else [],
            "totalCount": len(records) if is_search else REGISTRY_SIZE,
        }
        return httpx2.Response(200, json=body)

    return httpx2.MockTransport(handle)


async def serve(app: FastAPI) -> AsyncIterator[httpx2.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as http:
            yield http


@pytest.fixture
def seen() -> list[str]:
    return []


@pytest.fixture
def duchenne_records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = json.loads((FIXTURES / "duchenne_records.json").read_text("utf-8"))
    return records


@pytest.fixture
async def structured(
    duchenne_records: list[dict[str, Any]], seen: list[str]
) -> AsyncIterator[httpx2.AsyncClient]:
    app = create_app(Settings(), transport=registry(duchenne_records, seen))
    async for http in serve(app):
        yield http


async def test_a_structured_request_draws_the_phases_of_the_registry_records(
    structured: httpx2.AsyncClient,
) -> None:
    body = {
        "query": "How are Duchenne muscular dystrophy trials distributed across phases?",
        "condition": "Duchenne muscular dystrophy",
        "group_by": ["phase"],
        "options": {"planner": "structured"},
    }

    reply = await structured.post("/v1/query", json=body)

    assert reply.status_code == 200
    document = reply.json()
    RESPONSES.validate_python(document)
    assert document["kind"] == "visualization"
    assert document["visualization"]["type"] == "bar_chart"
    drawn = {row["phase"]: row["trial_count"] for row in document["visualization"]["data"]}
    assert drawn == DUCHENNE_PHASES
    assert document["meta"]["counts"]["series"][0]["trials_matched"] == 499


async def test_a_supplied_plan_gives_the_same_chart_without_a_model(structured: httpx2.AsyncClient) -> None:
    supplied = plan(
        entities=[{"kind": "condition", "value": "Duchenne muscular dystrophy", "role": "filter"}]
    )

    reply = await structured.post("/v1/analyses", json={"plan": supplied.model_dump(mode="json")})

    assert reply.status_code == 200
    assert reply.json()["meta"]["planner"]["mode"] == "supplied_plan"
    assert reply.json()["visualization"]["type"] == "bar_chart"


async def test_a_plan_that_names_nothing_is_a_clarification_and_asks_the_registry_nothing(
    seen: list[str],
) -> None:
    clarify = plan(
        entities=[],
        analysis={"kind": "clarify", "missing": ["drug_name"], "reason": "missing_entity"},
    )
    app = create_app(Settings(), planner=FakePlanner([clarify]), transport=registry([], seen))
    async for http in serve(app):
        reply = await http.post("/v1/query", json={"query": "How has the number of trials changed?"})

    assert reply.status_code == 200
    assert reply.json()["kind"] == "clarification"
    assert reply.json()["clarification"]["reason"] == "missing_entity"
    assert seen == []


async def test_a_name_that_matches_nothing_is_no_data() -> None:
    seen: list[str] = []
    app = create_app(Settings(), transport=registry([], seen))
    async for http in serve(app):
        reply = await http.post(
            "/v1/query",
            json={
                "query": "How many trials per year for xyzzumab?",
                "drug_name": "xyzzumab",
                "group_by": ["start_date"],
                "options": {"planner": "structured"},
            },
        )

    assert reply.status_code == 200
    assert reply.json()["kind"] == "no_data"
    assert "xyzzumab" in reply.json()["message"]


async def test_without_a_key_a_question_that_needs_a_model_is_a_503() -> None:
    app = create_app(Settings())
    async for http in serve(app):
        reply = await http.post("/v1/query", json={"query": "How are trials distributed across phases?"})

    assert reply.status_code == 503
    assert reply.json()["error"]["details"] == {"reason": "not_configured"}


async def test_the_capabilities_list_every_dimension_of_the_catalogue(structured: httpx2.AsyncClient) -> None:
    reply = await structured.get("/v1/capabilities")

    document = reply.json()
    assert {dimension["key"] for dimension in document["dimensions"]} >= {"phase", "start_date", "country"}
    assert document["planner"]["is_available"] is False


async def test_a_schema_is_served_by_name_and_an_unknown_name_is_a_404(
    structured: httpx2.AsyncClient,
) -> None:
    found = await structured.get("/v1/schema/contract")
    missing = await structured.get("/v1/schema/nothing")

    assert found.status_code == 200
    assert json.loads(found.text)["$defs"]
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"


async def test_the_recorded_examples_are_listed_and_loadable(tmp_path: Path) -> None:
    folder = tmp_path / "01-sample"
    folder.mkdir()
    for name, document in (("request", {"query": "q"}), ("plan", BASE), ("response", {"kind": "no_data"})):
        (folder / f"{name}.json").write_text(json.dumps(document), encoding="utf-8")
    app = create_app(Settings(examples_dir=tmp_path))
    async for http in serve(app):
        listing = await http.get("/v1/examples")
        one = await http.get("/v1/examples/01-sample")
        missing = await http.get("/v1/examples/..%2Fsecret")

    assert [item["slug"] for item in listing.json()] == ["01-sample"]
    assert one.json()["request"] == {"query": "q"}
    assert missing.status_code == 404
