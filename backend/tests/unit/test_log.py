"""Log output: the two formats, the level filter and the request id."""

import json
from typing import Any

import pytest
import structlog

from ctviz.log import add_request_id, configure_logging, request_id_var


def logged_json(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    """The single JSON line written to stdout so far."""
    (line,) = capsys.readouterr().out.splitlines()
    entry: dict[str, Any] = json.loads(line)
    return entry


def test_console_format_is_one_readable_line_without_colour_codes(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("console")

    structlog.get_logger().info("service_started", port=8000)

    (line,) = capsys.readouterr().out.splitlines()
    assert "service_started" in line
    assert "port=8000" in line
    assert "\x1b[" not in line  # stdout is not a terminal under pytest


def test_json_format_is_one_object_per_line(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("json")

    structlog.get_logger().warning("upstream_slow", duration_ms=1200)

    entry = logged_json(capsys)
    assert (entry["event"], entry["level"], entry["duration_ms"]) == ("upstream_slow", "warning", 1200)
    assert entry["timestamp"].endswith("Z")
    assert "request_id" not in entry


def test_json_format_renders_an_exception_as_text(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("json")

    structlog.get_logger().error("failed", exc_info=ValueError("boom"))

    assert "ValueError: boom" in logged_json(capsys)["exception"]


def test_console_format_prints_a_traceback_without_local_variables(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def call_provider(api_key: str) -> None:
        raise ValueError("the provider refused the request")

    configure_logging("console")

    try:
        # Joined here so that the value exists only in a frame's local variables, never in a source line.
        call_provider("sk-" + "held-in-a-local-variable")
    except ValueError as error:
        structlog.get_logger().error("failed", exc_info=error)

    logged = capsys.readouterr().out
    assert "ValueError: the provider refused the request" in logged
    assert "sk-held-in-a-local-variable" not in logged


def test_debug_lines_are_dropped(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("json")

    structlog.get_logger().debug("noise")

    assert capsys.readouterr().out == ""


def test_a_line_logged_during_a_request_carries_its_id(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("json")
    token = request_id_var.set("req-1")
    try:
        structlog.get_logger().info("inside")
    finally:
        request_id_var.reset(token)

    assert logged_json(capsys)["request_id"] == "req-1"


def test_an_id_given_by_the_caller_is_kept() -> None:
    token = request_id_var.set("from-context")
    try:
        event = add_request_id(None, "info", {"event": "x", "request_id": "from-caller"})
    finally:
        request_id_var.reset(token)

    assert event["request_id"] == "from-caller"
