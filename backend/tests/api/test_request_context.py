"""The request middleware: the request id and the deadline."""

import time
import uuid
from collections.abc import AsyncIterator

import anyio
import httpx2
import pytest
import structlog
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from structlog.testing import capture_logs

from ctviz.log import add_request_id, request_id_var

pytestmark = pytest.mark.anyio


# --- request id --------------------------------------------------------------------------------


async def test_a_request_without_an_id_is_given_a_new_one(client: httpx2.AsyncClient) -> None:
    first = (await client.get("/healthz")).headers["X-Request-ID"]
    second = (await client.get("/healthz")).headers["X-Request-ID"]

    assert uuid.UUID(first).version == 4
    assert first != second


async def test_the_id_a_client_sends_is_kept(client: httpx2.AsyncClient) -> None:
    response = await client.get("/healthz", headers={"X-Request-ID": "trace-0f3c7c1e/span:42"})

    assert response.headers.get_list("X-Request-ID") == ["trace-0f3c7c1e/span:42"]


@pytest.mark.parametrize("unusable", ["", "two words", "tab\tinside", "x" * 129])
async def test_an_id_that_is_not_safe_to_echo_is_replaced(unusable: str, client: httpx2.AsyncClient) -> None:
    response = await client.get("/healthz", headers={"X-Request-ID": unusable})

    assert uuid.UUID(response.headers["X-Request-ID"]).version == 4


async def test_an_error_response_carries_the_same_id_in_header_and_body(client: httpx2.AsyncClient) -> None:
    response = await client.get("/no-such-route", headers={"X-Request-ID": "abc-123"})

    assert response.headers.get_list("X-Request-ID") == ["abc-123"]
    assert response.json()["error"]["request_id"] == "abc-123"


async def work() -> None:
    await anyio.sleep(0.01)  # long enough for a concurrent request to begin in between
    structlog.get_logger().info("working")


async def test_a_line_logged_while_a_request_is_served_carries_its_id(
    app: FastAPI, client: httpx2.AsyncClient
) -> None:
    app.add_api_route("/work", work)

    with capture_logs(processors=[add_request_id]) as entries:
        response = await client.get("/work")

    assert [entry["request_id"] for entry in entries] == [response.headers["X-Request-ID"]]
    assert request_id_var.get() is None


async def test_concurrent_requests_log_under_their_own_ids(app: FastAPI, client: httpx2.AsyncClient) -> None:
    async def call(request_id: str) -> None:
        await client.get("/work", headers={"X-Request-ID": request_id})

    app.add_api_route("/work", work)

    with capture_logs(processors=[add_request_id]) as entries:
        async with anyio.create_task_group() as concurrently:
            concurrently.start_soon(call, "first")
            concurrently.start_soon(call, "second")

    assert sorted(entry["request_id"] for entry in entries) == ["first", "second"]


# --- deadline ----------------------------------------------------------------------------------


@pytest.mark.parametrize("request_deadline_s", [0.05])
async def test_a_request_that_overruns_its_deadline_is_cancelled_and_answered_504(
    app: FastAPI, client: httpx2.AsyncClient
) -> None:
    was_cancelled = False

    async def stall() -> None:
        nonlocal was_cancelled
        try:
            await anyio.sleep(30)
        except anyio.get_cancelled_exc_class():
            was_cancelled = True
            raise

    app.add_api_route("/stall", stall)

    started = time.monotonic()
    with capture_logs() as entries:
        response = await client.get("/stall")

    assert time.monotonic() - started < 5
    assert was_cancelled
    assert response.status_code == 504
    error = response.json()["error"]
    assert (error["code"], error["is_retryable"]) == ("deadline_exceeded", True)
    assert error["request_id"] == response.headers["X-Request-ID"]
    assert [(entry["event"], entry["log_level"]) for entry in entries] == [("deadline_exceeded", "warning")]


async def test_code_inside_a_request_can_read_how_long_it_has_left(
    app: FastAPI, client: httpx2.AsyncClient, request_deadline_s: float
) -> None:
    async def remaining() -> float:
        return anyio.current_effective_deadline() - anyio.current_time()

    app.add_api_route("/remaining", remaining)

    seconds_left = (await client.get("/remaining")).json()

    assert 0 < seconds_left <= request_deadline_s


@pytest.mark.parametrize("request_deadline_s", [0.2])
async def test_a_response_that_has_begun_is_allowed_to_finish(
    app: FastAPI, client: httpx2.AsyncClient
) -> None:
    async def two_chunks() -> AsyncIterator[bytes]:
        yield b"first,"
        await anyio.sleep(0.4)  # past the deadline, with the response already under way
        yield b"second"

    async def stream() -> StreamingResponse:
        return StreamingResponse(two_chunks(), media_type="text/plain")

    app.add_api_route("/stream", stream)

    response = await client.get("/stream")

    assert response.status_code == 200
    assert response.text == "first,second"
