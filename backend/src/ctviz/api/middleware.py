"""Request id and request deadline, as one pure ASGI middleware."""

import math
import re
import uuid
from typing import Final

import anyio
import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from structlog.typing import FilteringBoundLogger

from ctviz.api.handlers import app_error_response
from ctviz.errors import DeadlineExceeded
from ctviz.log import REQUEST_ID_HEADER, request_id_var

# A client's id is echoed into the logs and a response header, so only short, visible ASCII is taken.
_USABLE_REQUEST_ID: Final = re.compile(r"[\x21-\x7e]{1,128}")

_log: FilteringBoundLogger = structlog.get_logger()


class RequestContextMiddleware:
    """Gives every HTTP request an id and a deadline.

    The id is taken from `X-Request-ID` or created. It is bound into the log context, stored
    as `request.state.request_id` for the handlers, and returned in the `X-Request-ID` header.

    The deadline is a cancel scope around the rest of the application, so code inside can read
    it with `anyio.current_effective_deadline()`. When it passes before a response has begun,
    the work is cancelled and the client gets 504 `deadline_exceeded`.
    """

    def __init__(self, app: ASGIApp, *, deadline_s: float) -> None:
        self._app = app
        self._deadline_s = deadline_s

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request_id = _usable_or_new(Headers(scope=scope).get(REQUEST_ID_HEADER))
        scope.setdefault("state", {})["request_id"] = request_id
        deadline = anyio.move_on_after(self._deadline_s)
        response_started = False

        async def send_with_request_id(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                # A response that has begun is allowed to finish: cancelling it now would
                # put truncated JSON on the wire.
                deadline.deadline = math.inf
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        token = request_id_var.set(request_id)
        try:
            with deadline:
                await self._app(scope, receive, send_with_request_id)
            if deadline.cancelled_caught and not response_started:
                _log.warning("deadline_exceeded", deadline_s=self._deadline_s)
                error = DeadlineExceeded(f"The request did not finish within {self._deadline_s:g} seconds.")
                await app_error_response(error, request_id)(scope, receive, send_with_request_id)
        finally:
            request_id_var.reset(token)


def _usable_or_new(candidate: str | None) -> str:
    """The client's request id when it is safe to echo, otherwise a new one."""
    if candidate is not None and _USABLE_REQUEST_ID.fullmatch(candidate):
        return candidate
    return str(uuid.uuid4())
