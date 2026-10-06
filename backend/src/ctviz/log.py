"""Logging: the structlog configuration and the request-id context variable."""

import logging
import sys
from contextvars import ContextVar
from typing import Final, Literal

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

LogFormat = Literal["console", "json"]

# The request id on the wire: taken from this request header when the client sends one,
# and returned in the response header of the same name.
REQUEST_ID_HEADER: Final = "X-Request-ID"

# The request id in the logs: set by the request middleware for the length of one request, so
# that every line logged while it is being served carries the id without the caller passing it.
request_id_var: Final[ContextVar[str | None]] = ContextVar("ctviz_request_id", default=None)


def add_request_id(_logger: WrappedLogger, _method_name: str, event_dict: EventDict) -> EventDict:
    """structlog processor: add the current request id unless the caller gave one."""
    request_id = request_id_var.get()
    if request_id is not None:
        event_dict.setdefault("request_id", request_id)
    return event_dict


def configure_logging(log_format: LogFormat) -> None:
    """Configure structlog: aligned, readable lines for `console`, one JSON object per line for `json`."""
    processors: list[Processor] = [
        add_request_id,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    if log_format == "json":
        processors += [structlog.processors.format_exc_info, structlog.processors.JSONRenderer()]
    else:
        processors.append(
            structlog.dev.ConsoleRenderer(
                # Colour codes belong on a terminal only; they are noise in a redirected log.
                colors=sys.stdout.isatty(),
                # Left to its default, structlog switches to Rich wherever Rich happens to be
                # installed, and that formatter prints the local variables of every frame:
                # a key held in one of them would end up in the log.
                exception_formatter=structlog.dev.plain_traceback,
            )
        )
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
    )
