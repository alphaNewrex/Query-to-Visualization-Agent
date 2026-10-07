"""The service's failures: error codes, their HTTP mapping, and the exceptions that carry them.

A completed interpretation is always HTTP 200. Only failures of the service or of its
dependencies are HTTP errors, and every one of them is reported with a code from `ErrorCode`.
"""

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar, Final, Literal


class ErrorCode(StrEnum):
    """Every value the `code` field of the error body can take."""

    INVALID_REQUEST = "invalid_request"
    NOT_FOUND = "not_found"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    PLANNER_UNAVAILABLE = "planner_unavailable"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"
    UPSTREAM_RATE_LIMITED = "upstream_rate_limited"
    UPSTREAM_TIMEOUT = "upstream_timeout"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True)
class HttpMapping:
    """The HTTP status an error code is sent with, and whether repeating the request can help."""

    http_status: int
    is_retryable: bool


HTTP_MAPPING: Final[Mapping[ErrorCode, HttpMapping]] = MappingProxyType(
    {
        ErrorCode.INVALID_REQUEST: HttpMapping(422, is_retryable=False),
        ErrorCode.NOT_FOUND: HttpMapping(404, is_retryable=False),
        ErrorCode.METHOD_NOT_ALLOWED: HttpMapping(405, is_retryable=False),
        # Only a transient planner failure is retryable; PlannerUnavailableError decides per instance.
        ErrorCode.PLANNER_UNAVAILABLE: HttpMapping(503, is_retryable=False),
        ErrorCode.UPSTREAM_UNAVAILABLE: HttpMapping(502, is_retryable=True),
        ErrorCode.UPSTREAM_RATE_LIMITED: HttpMapping(503, is_retryable=True),
        ErrorCode.UPSTREAM_TIMEOUT: HttpMapping(504, is_retryable=True),
        ErrorCode.DEADLINE_EXCEEDED: HttpMapping(504, is_retryable=True),
        ErrorCode.INTERNAL_ERROR: HttpMapping(500, is_retryable=False),
    }
)

PlannerUnavailableReason = Literal["not_configured", "configuration", "transient"]


@dataclass(frozen=True)
class FieldError:
    """One entry of `details.errors` in an `invalid_request` body.

    `path` is a JSON Pointer to the offending value, relative to the request body: `/drug`.
    """

    path: str
    code: str
    message: str


class AppError(Exception):
    """A failure with a place in the error taxonomy.

    `message` is sent to the client, so it must never repeat a provider's error text,
    which may echo configuration.
    """

    code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
        is_retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        mapping = HTTP_MAPPING[self.code]
        self.message = message
        self.http_status = mapping.http_status
        self.is_retryable = mapping.is_retryable if is_retryable is None else is_retryable
        self.details: Mapping[str, object] = details or {}
        self.headers: Mapping[str, str] = headers or {}


class UpstreamRejectedQuery(AppError):
    """ClinicalTrials.gov answered 400 or 414 to a search this service built.

    Whether that is a fault of the service or of a word the user wrote that the registry reads as an
    operator is for the caller to say: a caller that can name the user's words turns it into an answer.
    """

    code = ErrorCode.INTERNAL_ERROR


class InvalidRequest(AppError):
    """The request broke a validation rule; `details.errors` names each one."""

    code = ErrorCode.INVALID_REQUEST

    def __init__(self, message: str, errors: Sequence[FieldError]) -> None:
        super().__init__(message, details={"errors": [asdict(error) for error in errors]})


class PlannerUnavailableError(AppError):
    """No model could write a plan; `details.reason` says why, and only `transient` is retryable."""

    code = ErrorCode.PLANNER_UNAVAILABLE

    def __init__(self, message: str, *, reason: PlannerUnavailableReason) -> None:
        super().__init__(message, details={"reason": reason}, is_retryable=reason == "transient")


class UpstreamUnavailable(AppError):
    """ClinicalTrials.gov answered 5xx, refused the connection or sent an unreadable body, after retries."""

    code = ErrorCode.UPSTREAM_UNAVAILABLE


class UpstreamRateLimited(AppError):
    """ClinicalTrials.gov kept throttling after backoff; the response carries `Retry-After`."""

    code = ErrorCode.UPSTREAM_RATE_LIMITED

    def __init__(self, message: str, *, retry_after_s: int) -> None:
        super().__init__(message, headers={"Retry-After": str(retry_after_s)})


class UpstreamTimeout(AppError):
    """ClinicalTrials.gov did not answer in time, after retries."""

    code = ErrorCode.UPSTREAM_TIMEOUT


class DeadlineExceeded(AppError):
    """The request ran past its own deadline."""

    code = ErrorCode.DEADLINE_EXCEEDED


class InvariantViolation(AppError):
    """A response failed a semantic invariant; it is withheld rather than sent invalid."""

    code = ErrorCode.INTERNAL_ERROR
