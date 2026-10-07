"""`ProgressStream`: the answer to `POST /v1/query/stream`, a `text/event-stream` of stages and one outcome.

The pipeline runs in a task of its own and reports through a callback, which puts each event on a queue;
this response writes the queue to the client. Three things end the work:

* the pipeline finishes, and the one `result` or `error` event is the last thing sent;
* the request deadline, which the request middleware stops enforcing once a response has begun, so it is
  enforced here, by a cancel scope with the deadline the route captured before the response began;
* the client going away: a disconnect message, or a failed write, cancels the pipeline's task.
"""

import json
import math
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Any, Final

import anyio
import structlog
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from fastapi.sse import format_sse_event
from pydantic_core import to_jsonable_python
from starlette.responses import Response
from starlette.types import Receive, Scope, Send
from structlog.typing import FilteringBoundLogger

from ctviz.api.handlers import error_envelope, internal_error_envelope
from ctviz.contract.response import QueryResponse
from ctviz.errors import AppError, DeadlineExceeded
from ctviz.progress import Progress, StageEvent

KEEPALIVE_S: Final = 15.0
HEADERS: Final = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",  # a reverse proxy must not hold the events back
}

# What the pipeline is run with: it reports progress and returns the response.
Run = Callable[[Progress], Awaitable[QueryResponse]]
_Item = tuple[str, Any]  # (event name, data)

_log: FilteringBoundLogger = structlog.get_logger()


class ProgressStream(Response):
    media_type = "text/event-stream"

    def __init__(self, run: Run, *, request_id: str, deadline: float) -> None:
        """`deadline` is an `anyio` clock time, read from the route before the response begins."""
        super().__init__(media_type=self.media_type, headers=HEADERS)
        # `Response` announces an empty body; this one has no length.
        self.raw_headers = [(name, value) for name, value in self.raw_headers if name != b"content-length"]
        self._run, self._request_id, self._deadline = run, request_id, deadline

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        events, queue = anyio.create_memory_object_stream[_Item](math.inf)

        async def pipeline() -> None:
            try:
                await self._produce(events)
            finally:
                events.close()

        async def watch_client() -> None:
            while (await receive())["type"] != "http.disconnect":
                pass
            group.cancel_scope.cancel()

        async def write() -> None:
            await send({"type": "http.response.start", "status": 200, "headers": self.raw_headers})
            async with queue:
                while True:
                    chunk = await _next_chunk(queue)
                    if chunk is None:
                        break
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            group.cancel_scope.cancel()

        try:
            async with anyio.create_task_group() as group:
                group.start_soon(pipeline)
                group.start_soon(watch_client)
                group.start_soon(write)
        except OSError:
            _log.info("stream_client_gone", request_id=self._request_id)  # a failed write: the client left

    async def _produce(self, events: MemoryObjectSendStream[_Item]) -> None:
        def progress(event: StageEvent) -> None:
            events.send_nowait(("stage", to_jsonable_python(asdict(event))))

        with anyio.CancelScope(deadline=self._deadline) as deadline:
            try:
                response = await self._run(progress)
            except AppError as error:
                events.send_nowait(("error", error_envelope(error, self._request_id)))
            except Exception:
                _log.exception("stream_failed", request_id=self._request_id)
                events.send_nowait(("error", internal_error_envelope(self._request_id)))
            else:
                events.send_nowait(("result", response.model_dump(mode="json")))
        if deadline.cancelled_caught:
            _log.warning("deadline_exceeded", request_id=self._request_id)
            message = "The request did not finish within its deadline."
            events.send_nowait(("error", error_envelope(DeadlineExceeded(message), self._request_id)))


async def _next_chunk(queue: MemoryObjectReceiveStream[_Item]) -> bytes | None:
    """The next event as wire bytes, a keepalive comment after a quiet spell, or None at the end."""
    try:
        with anyio.fail_after(KEEPALIVE_S):
            name, data = await queue.receive()
    except TimeoutError:
        return format_sse_event(comment="keepalive")
    except anyio.EndOfStream:
        return None
    return format_sse_event(event=name, data_str=json.dumps(data, ensure_ascii=False))
