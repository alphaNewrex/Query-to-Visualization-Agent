"""The application factory: configuration, start-up, compression and the documentation routes."""

import json
from pathlib import Path

import httpx2
import pytest
import structlog
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from starlette.types import Message
from structlog.testing import capture_logs

from ctviz.api.app import create_app

pytestmark = pytest.mark.anyio


async def test_the_factory_needs_no_arguments_and_no_env_file() -> None:
    transport = httpx2.ASGITransport(app=create_app())

    async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as http:
        response = await http.get("/healthz")

    assert response.status_code == 200


def test_the_factory_takes_its_settings_from_the_root_env_file(
    repository_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repository_root / ".env").write_text("CTVIZ_LOG_FORMAT=json\n", encoding="utf-8")

    create_app()
    structlog.get_logger().info("configured")

    (line,) = capsys.readouterr().out.splitlines()
    assert json.loads(line)["event"] == "configured"


async def start_and_stop(app: FastAPI) -> list[str]:
    """Run the ASGI lifespan as a server does, through every middleware; return the application's replies."""
    events = iter(["lifespan.startup", "lifespan.shutdown"])
    replies: list[str] = []

    async def receive() -> Message:
        return {"type": next(events)}

    async def send(message: Message) -> None:
        replies.append(message["type"])

    await app({"type": "lifespan", "asgi": {"version": "3.0"}, "state": {}}, receive, send)
    return replies


async def test_start_up_logs_the_source_of_each_owner_variable_and_warns_without_a_key() -> None:
    app = create_app()

    with capture_logs() as entries:
        replies = await start_and_stop(app)

    assert replies == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
    sources = {e["variable"]: e["source"] for e in entries if e["event"] == "configuration_source"}
    assert sources == {"OPENAI_API_KEY": "default", "OPENAI_API_BASE": "default", "ALLOWED_MODELS": "default"}
    assert [e["event"] for e in entries if e["log_level"] == "warning"] == ["planner_not_configured"]


@pytest.mark.parametrize(("size", "is_compressed"), [(999, False), (1000, True)])
async def test_responses_of_a_kilobyte_or_more_are_compressed(
    size: int, is_compressed: bool, app: FastAPI, client: httpx2.AsyncClient
) -> None:
    async def text() -> PlainTextResponse:
        return PlainTextResponse("x" * size)

    app.add_api_route("/text", text)

    response = await client.get("/text", headers={"Accept-Encoding": "gzip"})

    assert (response.headers.get("Content-Encoding") == "gzip") is is_compressed
    assert response.text == "x" * size
    assert "X-Request-ID" in response.headers


async def test_swagger_and_the_openapi_document_are_the_only_documentation_routes(
    client: httpx2.AsyncClient,
) -> None:
    document = (await client.get("/openapi.json")).json()

    assert document["openapi"].startswith("3.1")
    assert "/healthz" in document["paths"]
    assert (await client.get("/docs")).status_code == 200
    assert (await client.get("/redoc")).status_code == 404
    assert (await client.get("/docs/oauth2-redirect")).status_code == 404
