"""What the registry's answers mean: which failures are worth another attempt, and how a body is read.

An answer is a status and a body. Throttling, outages and unreadable bodies are `Transient`: they are
retried, and the service error they become if every attempt fails is chosen here (section 4.14).
"""

import math
from typing import Final, Literal

import anyio
import httpx2
import structlog
from structlog.typing import FilteringBoundLogger

from ctviz.ctgov.study import JsonObject, Study, parse_study
from ctviz.errors import AppError, UpstreamRateLimited, UpstreamTimeout, UpstreamUnavailable

_DEFAULT_RETRY_AFTER_S: Final = 10  # told to the caller when a throttling answer names no wait

_log: FilteringBoundLogger = structlog.get_logger()


class Transient(Exception):
    """An attempt failed in a way a later one may not; `kind` says which error follows if none succeeds."""

    def __init__(
        self, kind: Literal["timeout", "unavailable", "throttled"], retry_after_s: float | None = None
    ) -> None:
        super().__init__(kind)
        self.kind = kind
        self.retry_after_s = retry_after_s

    def as_service_error(self) -> AppError:
        match self.kind:
            case "timeout":
                return UpstreamTimeout("ClinicalTrials.gov did not answer in time.")
            case "throttled":
                wait = _DEFAULT_RETRY_AFTER_S if self.retry_after_s is None else math.ceil(self.retry_after_s)
                return UpstreamRateLimited("ClinicalTrials.gov is limiting requests.", retry_after_s=wait)
            case "unavailable":
                return UpstreamUnavailable(
                    "ClinicalTrials.gov could not be reached or sent an unreadable answer."
                )


def transient(
    url: str,
    kind: Literal["timeout", "unavailable", "throttled"],
    *,
    status: int | None = None,
    retry_after_s: float | None = None,
) -> Transient:
    """The failure of one attempt, logged; the caller raises it."""
    _log.warning("upstream_attempt_failed", url=url, kind=kind, status=status)
    return Transient(kind, retry_after_s)


def unreadable() -> UpstreamUnavailable:
    return UpstreamUnavailable("ClinicalTrials.gov sent an answer this service cannot read.")


def wait_before_retry(error: Exception) -> bool | float:
    """stamina's `on` hook: retry a transient failure, after the registry's `Retry-After` when it fits.

    A wait that would run past the request deadline is not waited for: the caller is better served by
    503 with the registry's own `Retry-After` than by 504 after the deadline.
    """
    if not isinstance(error, Transient):
        return False
    if error.retry_after_s is None:
        return True
    time_left = anyio.current_effective_deadline() - anyio.current_time()
    return error.retry_after_s if error.retry_after_s <= time_left else False


def retry_after(response: httpx2.Response) -> float | None:
    """`Retry-After` in seconds; the date form is not used by this registry and counts as absent."""
    try:
        return max(0.0, float(response.headers["Retry-After"]))
    except (KeyError, ValueError):
        return None


def total_count(body: JsonObject) -> int:
    """`totalCount`, which a request with `countTotal=true` always gets on its first page."""
    total = body.get("totalCount")
    if not isinstance(total, int) or isinstance(total, bool):
        raise unreadable()
    return total


def studies(body: JsonObject) -> tuple[Study, ...]:
    records = body.get("studies")
    if not isinstance(records, list):
        raise unreadable()
    try:
        return tuple(parse_study(record) for record in records if isinstance(record, dict))
    except ValueError as error:
        raise unreadable() from error


def next_token(body: JsonObject) -> str | None:
    token = body.get("nextPageToken")
    return token if isinstance(token, str) and token else None
