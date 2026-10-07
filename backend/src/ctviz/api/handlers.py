"""Exception handlers: every failure leaves the service in one error body.

    {"error": {"code", "message", "details", "request_id", "is_retryable"}}

FastAPI's own validation errors and Starlette's 404 and 405 are reshaped too, so a client
never has to parse a second error format.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Final

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from structlog.typing import FilteringBoundLogger

from ctviz.errors import AppError, ErrorCode, FieldError, InvalidRequest
from ctviz.log import REQUEST_ID_HEADER

_CODE_OF_STATUS: Final[Mapping[int, ErrorCode]] = {
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
}
_INTERNAL_ERROR_MESSAGE: Final = "The service failed to answer. Quote the request id when reporting it."

_log: FilteringBoundLogger = structlog.get_logger()


def install_exception_handlers(app: FastAPI) -> None:
    """Register one handler per source of failure."""
    app.add_exception_handler(AppError, handle_app_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    app.add_exception_handler(Exception, handle_unexpected_error)


def error_envelope(error: AppError, request_id: str) -> dict[str, object]:
    """The error body of a classified failure, as the data of an `error` event of the progress stream."""
    return _envelope(
        request_id,
        code=error.code,
        message=error.message,
        is_retryable=error.is_retryable,
        details=error.details,
    )


def internal_error_envelope(request_id: str) -> dict[str, object]:
    """The error body of anything unclassified."""
    return error_envelope(AppError(_INTERNAL_ERROR_MESSAGE), request_id)


def app_error_response(error: AppError, request_id: str) -> JSONResponse:
    """The error body for a classified failure."""
    return _error_response(
        request_id,
        status=error.http_status,
        code=error.code,
        message=error.message,
        is_retryable=error.is_retryable,
        details=error.details,
        headers=error.headers,
    )


async def handle_app_error(request: Request, exc: Exception) -> JSONResponse:
    """A failure the service raised on purpose, already classified."""
    assert isinstance(exc, AppError)
    if exc.http_status >= 500:
        _log.warning("request_failed", code=exc.code.value, http_status=exc.http_status)
    return app_error_response(exc, _request_id(request))


async def handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    """FastAPI's own 422, reshaped into one `{path, code, message}` entry per broken rule."""
    assert isinstance(exc, RequestValidationError)
    errors = [_field_error(error) for error in exc.errors()]
    return app_error_response(InvalidRequest(_summary(errors), errors), _request_id(request))


async def handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    """Starlette's 404 and 405, and any other HTTP error the framework or a route raises.

    The status is kept as raised. The code stays within the closed vocabulary: a client-side
    status without a code of its own is an `invalid_request`.
    """
    assert isinstance(exc, StarletteHTTPException)
    fallback = ErrorCode.INTERNAL_ERROR if exc.status_code >= 500 else ErrorCode.INVALID_REQUEST
    return _error_response(
        _request_id(request),
        status=exc.status_code,
        code=_CODE_OF_STATUS.get(exc.status_code, fallback),
        message=str(exc.detail),
        is_retryable=False,
        headers=exc.headers,
    )


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Anything unclassified: logged in full, reported to the client without detail."""
    request_id = _request_id(request)
    # Starlette runs this handler outside the request middleware, after the log context
    # was cleared, so the id is passed by hand.
    _log.error("unhandled_exception", request_id=request_id, exc_info=exc)
    return app_error_response(AppError(_INTERNAL_ERROR_MESSAGE), request_id)


def _error_response(
    request_id: str,
    *,
    status: int,
    code: ErrorCode,
    message: str,
    is_retryable: bool,
    details: Mapping[str, object] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Assemble the error response around the body of `_envelope`."""
    body = _envelope(request_id, code=code, message=message, is_retryable=is_retryable, details=details)
    # The middleware sets this header on every response that passes through it;
    # the response to an unexpected exception does not, so it is set here as well.
    return JSONResponse(body, status_code=status, headers={**(headers or {}), REQUEST_ID_HEADER: request_id})


def _envelope(
    request_id: str,
    *,
    code: ErrorCode,
    message: str,
    is_retryable: bool,
    details: Mapping[str, object] | None,
) -> dict[str, object]:
    """The error body. This is the only place that knows its shape."""
    error = {
        "code": code.value,
        "message": message,
        "details": dict(details or {}),
        "request_id": request_id,
        "is_retryable": is_retryable,
    }
    return {"error": error}


def _request_id(request: Request) -> str:
    """The id the request middleware stored on the request."""
    return str(request.state.request_id)


def _field_error(error: Mapping[str, Any]) -> FieldError:
    """One Pydantic error as a `details.errors` entry; the input value is never repeated."""
    code = str(error["type"])
    # FastAPI reports unparseable JSON at ("body", <character offset>), and an offset is not a path.
    location = error["loc"][:1] if code == "json_invalid" else error["loc"]
    message = "Unknown field." if code == "extra_forbidden" else str(error["msg"])
    return FieldError(path=_json_pointer(location), code=code, message=message)


def _json_pointer(location: Sequence[str | int]) -> str:
    """An RFC 6901 pointer for a Pydantic error location.

    A location inside the body is made relative to it (`("body", "drug")` becomes `/drug`);
    any other part of the request keeps its name as the first segment (`/query/limit`).
    """
    segments = location[1:] if tuple(location[:1]) == ("body",) else location
    return "".join("/" + str(segment).replace("~", "~0").replace("/", "~1") for segment in segments)


def _summary(errors: Sequence[FieldError]) -> str:
    """One sentence for `message`: the first broken rule, and how many more there are."""
    first = errors[0]
    where = f" at {first.path}" if first.path else ""
    more = f" ({len(errors) - 1} more in details.errors)" if len(errors) > 1 else ""
    return f"Invalid request{where}: {first.message}{more}"
