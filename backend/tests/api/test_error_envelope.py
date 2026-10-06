"""Every failure leaves the service in the one error body, whoever raised it.

The routes that fail are added by the tests: the service itself has no route yet that takes input.
"""

from collections.abc import AsyncIterator
from typing import Any

import httpx2
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict
from structlog.testing import capture_logs

from ctviz.errors import (
    AppError,
    DeadlineExceeded,
    FieldError,
    InvalidRequest,
    InvariantViolation,
    PlannerUnavailableError,
    UpstreamRateLimited,
    UpstreamTimeout,
    UpstreamUnavailable,
)

pytestmark = pytest.mark.anyio


class Comparison(BaseModel):
    values: list[int]


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")

    drug_name: str
    start_year: int | None = None
    compare: Comparison | None = None


async def ask(question: Question) -> Question:
    return question


async def page(limit: int) -> int:
    return limit


def error_of(response: httpx2.Response, *, status: int, code: str, is_retryable: bool) -> dict[str, Any]:
    """Check the shape every error body shares, and return its `error` object."""
    assert response.status_code == status
    assert response.headers["content-type"] == "application/json"
    body = response.json()
    assert set(body) == {"error"}
    error: dict[str, Any] = body["error"]
    assert set(error) == {"code", "message", "details", "request_id", "is_retryable"}
    assert (error["code"], error["is_retryable"]) == (code, is_retryable)
    assert error["message"]
    assert error["request_id"] == response.headers["X-Request-ID"]
    return error


@pytest.fixture
def app(app: FastAPI) -> FastAPI:
    app.add_api_route("/ask", ask, methods=["POST"])
    app.add_api_route("/page", page, methods=["GET"])
    return app


# --- Starlette's own errors --------------------------------------------------------------------


async def test_an_unknown_route_is_not_found(client: httpx2.AsyncClient) -> None:
    error = error_of(await client.get("/no-such-route"), status=404, code="not_found", is_retryable=False)

    assert error["details"] == {}


async def test_a_wrong_method_is_not_allowed_and_says_which_are(client: httpx2.AsyncClient) -> None:
    response = await client.post("/healthz")

    error_of(response, status=405, code="method_not_allowed", is_retryable=False)
    assert response.headers["Allow"] == "GET"


@pytest.mark.parametrize(
    ("status", "code"),
    [(409, "invalid_request"), (501, "internal_error")],
)
async def test_any_other_http_error_keeps_its_status_and_gets_a_code_from_the_closed_list(
    status: int, code: str, app: FastAPI, client: httpx2.AsyncClient
) -> None:
    async def refuse() -> None:
        raise HTTPException(status_code=status, detail="Refused for a reason.")

    app.add_api_route("/refuse", refuse)

    error = error_of(await client.get("/refuse"), status=status, code=code, is_retryable=False)

    assert error["message"] == "Refused for a reason."


# --- FastAPI's own 422 -------------------------------------------------------------------------


async def test_an_unknown_key_is_a_422_that_names_the_key(client: httpx2.AsyncClient) -> None:
    response = await client.post("/ask", json={"drug_name": "Pembrolizumab", "drug": "Keytruda"})

    error = error_of(response, status=422, code="invalid_request", is_retryable=False)
    assert error["details"] == {
        "errors": [{"path": "/drug", "code": "extra_forbidden", "message": "Unknown field."}]
    }
    assert "/drug" in error["message"]


async def test_every_broken_rule_is_listed_with_a_json_pointer(client: httpx2.AsyncClient) -> None:
    response = await client.post("/ask", json={"start_year": "soon", "compare": {"values": [1, "two"]}})

    error = error_of(response, status=422, code="invalid_request", is_retryable=False)
    listed = {(entry["path"], entry["code"]) for entry in error["details"]["errors"]}
    assert listed == {
        ("/drug_name", "missing"),
        ("/start_year", "int_parsing"),
        ("/compare/values/1", "int_parsing"),
    }
    assert all(set(entry) == {"path", "code", "message"} for entry in error["details"]["errors"])
    assert "2 more" in error["message"]


async def test_a_rejected_value_is_not_repeated_in_the_body(client: httpx2.AsyncClient) -> None:
    response = await client.post("/ask", json={"drug_name": "x", "start_year": "rejected-input"})

    error_of(response, status=422, code="invalid_request", is_retryable=False)
    assert "rejected-input" not in response.text


async def test_a_key_with_pointer_syntax_in_it_is_escaped(client: httpx2.AsyncClient) -> None:
    response = await client.post("/ask", json={"drug_name": "x", "a/b~c": 1})

    error = error_of(response, status=422, code="invalid_request", is_retryable=False)
    assert error["details"]["errors"][0]["path"] == "/a~1b~0c"


async def test_a_body_that_is_not_json_points_at_the_whole_body(client: httpx2.AsyncClient) -> None:
    response = await client.post(
        "/ask", content=b'{"drug_name": ', headers={"Content-Type": "application/json"}
    )

    error = error_of(response, status=422, code="invalid_request", is_retryable=False)
    assert [(entry["path"], entry["code"]) for entry in error["details"]["errors"]] == [("", "json_invalid")]


async def test_an_error_outside_the_body_keeps_its_part_of_the_request(client: httpx2.AsyncClient) -> None:
    response = await client.get("/page", params={"limit": "many"})

    error = error_of(response, status=422, code="invalid_request", is_retryable=False)
    assert error["details"]["errors"][0]["path"] == "/query/limit"


# --- the service's own failures ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("raised", "status", "code", "is_retryable", "details"),
    [
        (
            InvalidRequest("Inverted years.", [FieldError("/start_year", "inverted_years", "Too late.")]),
            422,
            "invalid_request",
            False,
            {"errors": [{"path": "/start_year", "code": "inverted_years", "message": "Too late."}]},
        ),
        (
            PlannerUnavailableError("No model is configured.", reason="not_configured"),
            503,
            "planner_unavailable",
            False,
            {"reason": "not_configured"},
        ),
        (
            PlannerUnavailableError("The model did not answer.", reason="transient"),
            503,
            "planner_unavailable",
            True,
            {"reason": "transient"},
        ),
        (UpstreamUnavailable("ClinicalTrials.gov failed."), 502, "upstream_unavailable", True, {}),
        (UpstreamTimeout("ClinicalTrials.gov was slow."), 504, "upstream_timeout", True, {}),
        (DeadlineExceeded("Out of time."), 504, "deadline_exceeded", True, {}),
        (InvariantViolation("The response was withheld."), 500, "internal_error", False, {}),
    ],
)
async def test_a_failure_the_service_raises_is_sent_as_classified(
    raised: AppError,
    status: int,
    code: str,
    is_retryable: bool,
    details: dict[str, Any],
    app: FastAPI,
    client: httpx2.AsyncClient,
) -> None:
    async def fail() -> None:
        raise raised

    app.add_api_route("/fail", fail)

    error = error_of(await client.get("/fail"), status=status, code=code, is_retryable=is_retryable)

    assert error["message"] == raised.message
    assert error["details"] == details


async def test_a_rate_limit_sets_retry_after(app: FastAPI, client: httpx2.AsyncClient) -> None:
    async def fail() -> None:
        raise UpstreamRateLimited("ClinicalTrials.gov is throttling.", retry_after_s=7)

    app.add_api_route("/fail", fail)

    response = await client.get("/fail")

    error_of(response, status=503, code="upstream_rate_limited", is_retryable=True)
    assert response.headers["Retry-After"] == "7"


@pytest.mark.parametrize(
    ("raised", "code", "status"),
    [
        (UpstreamUnavailable("ClinicalTrials.gov failed."), "upstream_unavailable", 502),
        (InvariantViolation("The response was withheld."), "internal_error", 500),
    ],
)
async def test_a_server_side_failure_is_logged_with_its_code(
    raised: AppError, code: str, status: int, app: FastAPI, client: httpx2.AsyncClient
) -> None:
    async def fail() -> None:
        raise raised

    app.add_api_route("/fail", fail)

    with capture_logs() as entries:
        await client.get("/fail")

    (entry,) = entries
    assert (entry["event"], entry["log_level"]) == ("request_failed", "warning")
    assert (entry["code"], entry["http_status"]) == (code, status)


async def test_a_request_the_client_got_wrong_is_not_logged_as_a_failure(
    app: FastAPI, client: httpx2.AsyncClient
) -> None:
    async def fail() -> None:
        raise InvalidRequest("Inverted years.", [FieldError("/start_year", "inverted_years", "Too late.")])

    app.add_api_route("/fail", fail)

    with capture_logs() as entries:
        await client.get("/fail")

    assert entries == []


# --- anything unexpected -----------------------------------------------------------------------


@pytest.fixture
async def client_that_survives_crashes(app: FastAPI) -> AsyncIterator[httpx2.AsyncClient]:
    """Starlette re-raises an unhandled exception after answering, so that a server can log it.

    The default test transport would raise it into the test instead of returning the response.
    """
    transport = httpx2.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


async def test_an_unexpected_exception_is_a_500_that_keeps_its_text_to_the_log(
    app: FastAPI, client_that_survives_crashes: httpx2.AsyncClient
) -> None:
    async def crash() -> None:
        raise RuntimeError("connection string with a password")

    app.add_api_route("/crash", crash)

    with capture_logs() as entries:
        response = await client_that_survives_crashes.get("/crash")

    error = error_of(response, status=500, code="internal_error", is_retryable=False)
    assert "password" not in response.text
    (entry,) = entries
    assert (entry["event"], entry["log_level"]) == ("unhandled_exception", "error")
    assert entry["request_id"] == error["request_id"]
    assert isinstance(entry["exc_info"], RuntimeError)
