"""The error taxonomy: every code with its HTTP status and whether a retry can help."""

import pytest

from ctviz.errors import (
    HTTP_MAPPING,
    AppError,
    DeadlineExceeded,
    ErrorCode,
    FieldError,
    InvalidRequest,
    InvariantViolation,
    PlannerUnavailableError,
    PlannerUnavailableReason,
    UpstreamRateLimited,
    UpstreamTimeout,
    UpstreamUnavailable,
)

# code, HTTP status, retryable: one row per documented error code.
DOCUMENTED = {
    ("invalid_request", 422, False),
    ("not_found", 404, False),
    ("method_not_allowed", 405, False),
    ("planner_unavailable", 503, False),
    ("upstream_unavailable", 502, True),
    ("upstream_rate_limited", 503, True),
    ("upstream_timeout", 504, True),
    ("deadline_exceeded", 504, True),
    ("internal_error", 500, False),
}


def test_the_mapping_table_holds_exactly_the_documented_codes() -> None:
    table = {(code.value, row.http_status, row.is_retryable) for code, row in HTTP_MAPPING.items()}

    assert table == DOCUMENTED
    assert set(HTTP_MAPPING) == set(ErrorCode)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (InvalidRequest("m", [FieldError("/drug", "extra_forbidden", "Unknown field.")]), "invalid_request"),
        (PlannerUnavailableError("m", reason="configuration"), "planner_unavailable"),
        (UpstreamUnavailable("m"), "upstream_unavailable"),
        (UpstreamRateLimited("m", retry_after_s=3), "upstream_rate_limited"),
        (UpstreamTimeout("m"), "upstream_timeout"),
        (DeadlineExceeded("m"), "deadline_exceeded"),
        (InvariantViolation("m"), "internal_error"),
        (AppError("m"), "internal_error"),
    ],
)
def test_every_exception_takes_its_status_and_retryability_from_the_table(error: AppError, code: str) -> None:
    row = HTTP_MAPPING[ErrorCode(code)]

    assert error.code.value == code
    assert (error.http_status, error.is_retryable) == (row.http_status, row.is_retryable)
    assert error.message == str(error) == "m"


@pytest.mark.parametrize(
    ("reason", "is_retryable"),
    [("not_configured", False), ("configuration", False), ("transient", True)],
)
def test_a_planner_failure_is_retryable_only_when_transient(
    reason: PlannerUnavailableReason, is_retryable: bool
) -> None:
    error = PlannerUnavailableError("No planner.", reason=reason)

    assert error.details == {"reason": reason}
    assert error.is_retryable is is_retryable


def test_an_invalid_request_lists_each_broken_rule() -> None:
    error = InvalidRequest("One problem.", [FieldError("/drug", "extra_forbidden", "Unknown field.")])

    assert error.details == {
        "errors": [{"path": "/drug", "code": "extra_forbidden", "message": "Unknown field."}]
    }


def test_a_rate_limit_tells_the_client_when_to_come_back() -> None:
    assert UpstreamRateLimited("Throttled.", retry_after_s=7).headers == {"Retry-After": "7"}
