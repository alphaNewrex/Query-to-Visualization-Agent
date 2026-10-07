"""POST /v1/query/stream: the events, the headers, the error event, the deadline and a client that leaves."""

import json
from collections.abc import AsyncIterator
from typing import Any

import anyio
import httpx2
import pytest

from ctviz.api.app import create_app
from ctviz.api.stream import ProgressStream
from ctviz.contract.response import QueryResponse
from ctviz.errors import PlannerUnavailableError
from ctviz.planning.planner import FakePlanner
from ctviz.progress import Progress, StageEvent
from ctviz.settings import Settings
from tests.api.test_query import DUCHENNE_PHASES, FIXTURES, registry, serve
from tests.unit.plan_samples import plan

pytestmark = pytest.mark.anyio

BODY = {
    "query": "How are Duchenne muscular dystrophy trials distributed across phases?",
    "condition": "Duchenne muscular dystrophy",
    "group_by": ["phase"],
    "options": {"planner": "structured", "use_cache": False},
}


def parse(text: str) -> list[tuple[str, Any]]:
    events = []
    for block in text.split("\n\n"):
        lines = [line for line in block.split("\n") if line and not line.startswith(":")]
        if lines:
            name = next(line[7:] for line in lines if line.startswith("event: "))
            data = json.loads("".join(line[6:] for line in lines if line.startswith("data: ")))
            events.append((name, data))
    return events


@pytest.fixture
async def structured() -> AsyncIterator[httpx2.AsyncClient]:
    records = json.loads((FIXTURES / "duchenne_records.json").read_text("utf-8"))
    app = create_app(Settings(), transport=registry(records, []))
    async for http in serve(app):
        yield http


async def test_the_stream_reports_every_stage_and_ends_with_the_same_body_as_query(
    structured: httpx2.AsyncClient,
) -> None:
    reply = await structured.post("/v1/query/stream", json=BODY)
    plain = await structured.post("/v1/query", json=BODY)

    assert reply.status_code == 200
    assert reply.headers["content-type"].startswith("text/event-stream")
    assert reply.headers["cache-control"] == "no-cache, no-transform"
    events = parse(reply.text)
    assert [name for name, _ in events[:-1]] == ["stage"] * (len(events) - 1)
    assert events[-1][0] == "result"
    steps = [(data["step"], data["status"]) for _, data in events[:-1]]
    assert {step for step, _ in steps} == {"plan", "check", "resolve", "strategy", "execute", "build"}
    assert all(set(data) == {"step", "status", "summary", "detail"} for _, data in events[:-1])
    resolve_done = next(d for _, d in events if d.get("step") == "resolve" and d["status"] == "done")
    assert resolve_done["summary"].startswith("Duchenne muscular dystrophy: ")
    result = events[-1][1]
    document = plain.json()
    drawn = {row["phase"]: row["trial_count"] for row in result["visualization"]["data"]}
    assert drawn == DUCHENNE_PHASES
    assert result["visualization"] == document["visualization"]
    assert result["meta"]["plan"] == document["meta"]["plan"]
    assert result["meta"]["conversation"] == {"is_follow_up": False, "carried_over": [], "changed": []}


async def test_the_stages_come_in_order_and_each_started_stage_is_done(
    structured: httpx2.AsyncClient,
) -> None:
    events = parse((await structured.post("/v1/query/stream", json=BODY)).text)
    stages = [(d["step"], d["status"]) for name, d in events if name == "stage"]
    order = ["plan", "check", "resolve", "strategy", "execute", "build"]
    firsts = [stages.index((step, "started")) for step in order]
    assert firsts == sorted(firsts)
    for step in order:
        assert stages.index((step, "done")) > stages.index((step, "started"))
        assert stages.index((step, "done")) == max(i for i, s in enumerate(stages) if s[0] == step)


async def test_a_clarification_is_a_result_after_the_plan_and_check_stages() -> None:
    clarify = plan(
        entities=[], analysis={"kind": "clarify", "missing": ["drug_name"], "reason": "missing_entity"}
    )
    app = create_app(Settings(), planner=FakePlanner([clarify]), transport=registry([], []))
    async for http in serve(app):
        reply = await http.post("/v1/query/stream", json={"query": "How has the number changed?"})

    events = parse(reply.text)
    assert [(n, d.get("step")) for n, d in events] == [
        ("stage", "plan"),
        ("stage", "plan"),
        ("stage", "check"),
        ("stage", "check"),
        ("result", None),
    ]
    assert events[-1][1]["kind"] == "clarification"
    assert events[3][1]["summary"].startswith("decided without data: clarification")


async def test_a_failure_after_the_stream_began_is_one_error_event() -> None:
    app = create_app(Settings())  # no model is configured
    async for http in serve(app):
        reply = await http.post("/v1/query/stream", json={"query": "How are trials distributed by phase?"})

    assert reply.status_code == 200
    events = parse(reply.text)
    assert [name for name, _ in events] == ["stage", "error"]
    error = events[-1][1]["error"]
    assert error["code"] == "planner_unavailable" and error["details"] == {"reason": "not_configured"}
    assert error["request_id"] == reply.headers["x-request-id"]


async def test_a_bad_body_is_still_an_ordinary_422(structured: httpx2.AsyncClient) -> None:
    reply = await structured.post("/v1/query/stream", json={"query": "12"})
    assert reply.status_code == 422 and reply.json()["error"]["code"] == "invalid_request"


# --- the response, driven directly --------------------------------------------------------------------


class Wire:
    """An ASGI exchange by hand: what was sent, and when the client leaves."""

    def __init__(self, leaves_after_s: float | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self._leaves_after_s = leaves_after_s

    async def receive(self) -> dict[str, Any]:
        if self._leaves_after_s is None:
            await anyio.sleep_forever()
        await anyio.sleep(self._leaves_after_s)
        return {"type": "http.disconnect"}

    async def send(self, message: dict[str, Any]) -> None:
        self.sent.append(message)

    @property
    def text(self) -> str:
        return b"".join(m.get("body", b"") for m in self.sent).decode()


async def drive(stream: ProgressStream, wire: Wire) -> None:
    await stream({"type": "http", "asgi": {"spec_version": "2.3"}}, wire.receive, wire.send)


async def test_the_deadline_ends_the_stream_with_a_deadline_error_event() -> None:
    cancelled = anyio.Event()

    async def never(progress: Progress) -> QueryResponse:
        progress(StageEvent("plan", "started", "Reading"))
        try:
            await anyio.sleep_forever()
        finally:
            cancelled.set()

    wire = Wire()
    stream = ProgressStream(never, request_id="r1", deadline=anyio.current_time() + 0.05)
    with anyio.fail_after(5):
        await drive(stream, wire)

    assert cancelled.is_set()
    assert [name for name, _ in parse(wire.text)] == ["stage", "error"]
    assert parse(wire.text)[-1][1]["error"]["code"] == "deadline_exceeded"
    headers = dict(wire.sent[0]["headers"])
    assert headers[b"cache-control"] == b"no-cache, no-transform" and b"content-length" not in headers


async def test_a_client_that_leaves_cancels_the_pipeline() -> None:
    cancelled = anyio.Event()

    async def slow(progress: Progress) -> QueryResponse:
        progress(StageEvent("plan", "started", "Reading"))
        try:
            await anyio.sleep_forever()
        finally:
            cancelled.set()

    wire = Wire(leaves_after_s=0.05)
    stream = ProgressStream(slow, request_id="r2", deadline=float("inf"))
    with anyio.fail_after(5):
        await drive(stream, wire)

    assert cancelled.is_set()
    assert "event: result" not in wire.text and "event: error" not in wire.text


async def test_an_unclassified_failure_is_an_internal_error_event_without_detail() -> None:
    async def broken(progress: Progress) -> QueryResponse:
        raise RuntimeError("secret detail")

    wire = Wire()
    with anyio.fail_after(5):
        await drive(ProgressStream(broken, request_id="r3", deadline=float("inf")), wire)

    error = parse(wire.text)[-1][1]["error"]
    assert error["code"] == "internal_error" and "secret" not in wire.text


async def test_a_classified_failure_keeps_its_code() -> None:
    async def unavailable(progress: Progress) -> QueryResponse:
        raise PlannerUnavailableError("No planning model.", reason="transient")

    wire = Wire()
    with anyio.fail_after(5):
        await drive(ProgressStream(unavailable, request_id="r4", deadline=float("inf")), wire)

    assert parse(wire.text)[-1][1]["error"]["details"] == {"reason": "transient"}
