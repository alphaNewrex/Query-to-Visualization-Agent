"""The ClinicalTrials.gov client: every number the service reports comes through it.

One pooled HTTP client sits behind a concurrency cap and a token bucket. A failed attempt is retried with
backoff, a result is cached under the registry's data timestamp, and identical requests that are in flight
at the same time share one run. What the registry answers with is turned into the service's own errors
(section 4.14 of the plan), and every request is written to the log of the request it was made for.
"""

import itertools
import math
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final, Protocol

import anyio
import httpx2
import stamina
import structlog
from structlog.typing import FilteringBoundLogger

from ctviz.contract.response import Origin, UpstreamRequest
from ctviz.ctgov import answers
from ctviz.ctgov.cache import SingleFlightCache
from ctviz.ctgov.params import Params, canonical_url
from ctviz.ctgov.partition import PIECE, SEQUENTIAL_PAGES, Partition, as_day, partition
from ctviz.ctgov.ratelimit import TokenBucket
from ctviz.ctgov.study import JsonObject, Study
from ctviz.errors import UpstreamRejectedQuery
from ctviz.settings import Settings

_USER_AGENT: Final = "ctviz/0.1 (ClinicalTrials.gov query-to-visualization take-home)"
_CONNECT_TIMEOUT_S: Final = 5.0
_READ_TIMEOUT_S: Final = 20.0
_ATTEMPTS: Final = 3
_MAX_PAGE_SIZE: Final = 1000  # a larger `pageSize` is silently clamped by the registry
_VERSION_TTL_S: Final = 300.0
_PAGE_CACHE_SIZE: Final = 1024
# The walk cache is bounded by the trials it holds, not by the walks: one walk can be tens of thousands.
_WALK_CACHE_TRIALS: Final = 60_000
# How many ranges per concurrent request a split aims at, so that a long range does not end the walk alone.
_RANGES_PER_SLOT: Final = 3
_THROTTLE_MEMORY_S: Final = 600.0  # how long after a 429 or 403 the registry is treated as touchy

_log: FilteringBoundLogger = structlog.get_logger()


@dataclass(frozen=True)
class ApiVersion:
    """`GET /version`, verbatim. The data timestamp has no offset."""

    api_version: str
    data_timestamp: str


@dataclass(frozen=True)
class Page:
    """One response of `/studies`: the exact number of matching trials, and some of them."""

    total: int
    studies: tuple[Study, ...]
    url: str  # the URL that was called; it returns exactly these trials


@dataclass(frozen=True)
class WalkResult:
    """Every trial of a paged walk, each once."""

    studies: tuple[Study, ...]
    total: int  # the registry's exact count of the search, taken before the first page was read

    @property
    def is_consistent(self) -> bool:
        """Every counted trial was read, and no other.

        A registry refresh during the walk breaks it: pages shift, and the trials read are no longer
        the trials counted. The caller says so in the response (`walk_count_mismatch`).
        """
        return len(self.studies) == self.total


class RequestLog(Protocol):
    """What the client needs from the context of the request it works for."""

    def log_request(self, entry: UpstreamRequest) -> None:
        """Append `entry`. It is called when the request is issued, so the log keeps the order of issue."""

    def note_studies(self, studies: Iterable[Study]) -> None:
        """Remember that the registry returned these trials during the request."""


class CtGovClient:
    """Typed, read-only access to the registry's `/studies` and `/version`.

    Four request forms are built on one search (`Params`): `count`, `sample`, `walk` and `sorted_page`.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
    ) -> None:
        self._base_url = settings.ctgov_base_url.rstrip("/")
        self._clock = clock
        self._http = httpx2.AsyncClient(
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            timeout=httpx2.Timeout(_CONNECT_TIMEOUT_S, read=_READ_TIMEOUT_S),
            limits=httpx2.Limits(max_connections=settings.ctgov_concurrency),
            transport=transport,
        )
        self._max_concurrency = self._concurrency = settings.ctgov_concurrency
        self._slots = anyio.CapacityLimiter(self._concurrency)
        self._bucket = TokenBucket(settings.ctgov_burst, settings.ctgov_rate_per_s, clock, sleep)
        self._throttled_until: float | None = None
        self._retrying = stamina.AsyncRetryingCaller(
            attempts=_ATTEMPTS, timeout=None, wait_initial=0.25, wait_max=4.0, wait_jitter=0.25
        )
        self._pages = SingleFlightCache[Page](_PAGE_CACHE_SIZE, settings.cache_ttl_s, clock)
        self._walks = SingleFlightCache[WalkResult](
            _WALK_CACHE_TRIALS, settings.cache_ttl_s, clock, getsizeof=lambda walk: max(1, len(walk.studies))
        )
        self._version: ApiVersion | None = None
        self._version_expires = 0.0
        self._version_lock = anyio.Lock()

    async def aclose(self) -> None:
        await self._http.aclose()

    @property
    def is_throttled(self) -> bool:
        """The registry answered 429 or 403 within the last ten minutes: prefer walks to fan-outs."""
        return self._throttled_until is not None and self._clock() < self._throttled_until

    async def version(self) -> ApiVersion:
        """The API version and data timestamp, cached for five minutes."""
        if self._version is not None and self._clock() < self._version_expires:
            return self._version
        async with self._version_lock:
            if self._version is None or self._clock() >= self._version_expires:
                self._version = _version_of(await self._get_json(f"{self._base_url}/version"))
                self._version_expires = self._clock() + _VERSION_TTL_S
            return self._version

    async def registry_size(self) -> int:
        """The number of studies in the registry: an unfiltered count, cached like any other."""
        page, _ = await self._cached_page(self._url(Params(), ("NCTId",), 1, None))
        return page.total

    async def count(self, params: Params, ctx: RequestLog, *, origin: Origin) -> int:
        """How many trials match. Exact whatever the size, and as fast for a large result as a small one."""
        # One record is the least a page can hold: `pageSize=0` would return ten. It is not kept, because
        # nothing is cited from a count.
        page = await self._logged_page(params, ctx, ("NCTId",), 1, None, origin, keeps_studies=False)
        return page.total

    async def sample(
        self,
        params: Params,
        ctx: RequestLog,
        *,
        fields: Sequence[str],
        page_size: int,
        sort: str | None,
        origin: Origin,
    ) -> Page:
        """The exact count and the first `page_size` trials, in one request."""
        return await self._logged_page(params, ctx, fields, page_size, sort, origin, keeps_studies=True)

    async def sorted_page(
        self, params: Params, ctx: RequestLog, *, fields: Sequence[str], sort: str, page_size: int
    ) -> Page:
        """One page in a date or numeric order, such as `EnrollmentCount:desc`."""
        return await self._logged_page(params, ctx, fields, page_size, sort, "execution", keeps_studies=True)

    async def walk(self, params: Params, ctx: RequestLog, *, fields: Sequence[str]) -> WalkResult:
        """Every matching trial, read page by page.

        A search of more than a few pages is split into disjoint ranges of first-posted date, and the
        ranges are walked at the same time, each with the token chain of its own. Every page is requested
        with the parameters of the first and the token of the page before it: the registry answers a token
        used with other parameters with HTTP 200 and the wrong data.
        """
        first_url = self._url(params, fields, _MAX_PAGE_SIZE, None)
        key = ((await self.version()).data_timestamp, first_url)
        result, is_shared = await self._walks.get(
            key,
            lambda: self._read_all(params, ctx, fields),
            keep=lambda walk: len(walk.studies) <= _WALK_CACHE_TRIALS,
        )
        if is_shared:
            entry = _issue(ctx, first_url, "execution")
            self._complete(entry, self._clock(), result.total, result.studies, is_cached=True)
            ctx.note_studies(result.studies)
        return result

    # --- pages -----------------------------------------------------------------------------------------

    def _url(
        self,
        params: Params,
        fields: Sequence[str],
        page_size: int,
        sort: str | None,
        token: str | None = None,
    ) -> str:
        return canonical_url(
            self._base_url,
            params,
            count_total=True,
            page_size=page_size,
            fields=fields,
            sort=sort,
            page_token=token,
        )

    async def _logged_page(
        self,
        params: Params,
        ctx: RequestLog,
        fields: Sequence[str],
        page_size: int,
        sort: str | None,
        origin: Origin,
        *,
        keeps_studies: bool,
    ) -> Page:
        if not 1 <= page_size <= _MAX_PAGE_SIZE:
            raise ValueError(f"A page holds 1 to {_MAX_PAGE_SIZE} trials, not {page_size}.")
        url = self._url(params, fields, page_size, sort)
        # Logged before anything is awaited, so concurrent requests keep the order they were issued in.
        entry = _issue(ctx, url, origin)
        started = self._clock()
        page, is_shared = await self._cached_page(url)
        self._complete(entry, started, page.total, page.studies, is_cached=is_shared)
        if keeps_studies:
            ctx.note_studies(page.studies)
        return page

    async def _cached_page(self, url: str) -> tuple[Page, bool]:
        """The page, and whether the cache or another request's fetch supplied it."""
        key = ((await self.version()).data_timestamp, url)
        return await self._pages.get(key, lambda: self._download_page(url))

    async def _download_page(self, url: str) -> Page:
        body = await self._get_json(url)
        return Page(total=answers.total_count(body), studies=answers.studies(body), url=url)

    async def _read_all(self, params: Params, ctx: RequestLog, fields: Sequence[str]) -> WalkResult:
        started = self._clock()
        total = await self.count(params, ctx, origin="execution")
        ranges = await self._ranges(params, total, ctx)
        chains: list[dict[str, Study]] = [{} for _ in ranges]

        async def read(position: int, part: Partition) -> None:
            chains[position] = await self._read_chain(part.params, ctx, fields)

        try:
            async with anyio.create_task_group() as group:
                for position, part in enumerate(ranges):
                    group.start_soon(read, position, part)
        except ExceptionGroup as failures:
            raise _first_failure(failures) from failures
        # Keyed by NCT ID: a data refresh during a walk can show a trial on two pages, and it counts once.
        trials: dict[str, Study] = {}
        for chain in chains:
            for nct_id, study in chain.items():
                trials.setdefault(nct_id, study)
        _log.info(
            "walk_finished",
            total=total,
            read=len(trials),
            ranges=len(ranges),
            seconds=round(self._clock() - started, 2),
        )
        return WalkResult(studies=tuple(trials.values()), total=total)

    async def _ranges(self, params: Params, total: int, ctx: RequestLog) -> list[Partition]:
        """The searches to walk: the whole search when it is short, otherwise disjoint date ranges."""
        whole = [Partition(params, total)]
        if total <= SEQUENTIAL_PAGES * _MAX_PAGE_SIZE:
            return whole
        # The oldest and the newest first-posted trial, which bound the days that are worth cutting.
        oldest, newest = await self._ends(params, ctx)
        if oldest is None or newest is None:
            return whole
        slots = max(1, self._concurrency) * _RANGES_PER_SLOT
        pages_per_range = max(1, math.ceil(total / slots / _MAX_PAGE_SIZE))

        async def count(narrowed: Params) -> int:
            return await self.count(narrowed, ctx, origin="execution")

        return await partition(
            params, total, oldest, newest, leaf_max=pages_per_range * _MAX_PAGE_SIZE, count=count
        )

    async def _ends(self, params: Params, ctx: RequestLog) -> tuple[date | None, date | None]:
        ends: dict[str, date | None] = {}

        async def one(order: str) -> None:
            page = await self._logged_page(
                params, ctx, (PIECE,), 1, f"{PIECE}:{order}", "execution", keeps_studies=False
            )
            posted = page.studies[0].first_post_date if page.studies else None
            ends[order] = None if posted is None else as_day(posted.date)

        try:
            async with anyio.create_task_group() as group:
                group.start_soon(one, "asc")
                group.start_soon(one, "desc")
        except ExceptionGroup as failures:
            raise _first_failure(failures) from failures
        return ends["asc"], ends["desc"]

    async def _read_chain(self, params: Params, ctx: RequestLog, fields: Sequence[str]) -> dict[str, Study]:
        """The trials of one search, by NCT ID, read as one chain of pages."""
        trials: dict[str, Study] = {}
        total = 0
        token: str | None = None
        for page_number in itertools.count():
            entry = _issue(ctx, self._url(params, fields, _MAX_PAGE_SIZE, None, token), "execution")
            started = self._clock()
            body = await self._get_json(entry.url)
            page = answers.studies(body)
            if page_number == 0:
                total = answers.total_count(body)
            self._complete(entry, started, total if page_number == 0 else None, page)
            ctx.note_studies(page)
            for study in page:
                trials.setdefault(study.nct_id, study)
            token = answers.next_token(body)
            # Every counted trial is read: a full last page's token would only lead to an empty page.
            if token is None or not page or len(trials) >= total:
                break
        return trials

    def _complete(
        self,
        entry: UpstreamRequest,
        started: float,
        total: int | None,
        records: Sequence[Study],
        *,
        is_cached: bool = False,
    ) -> None:
        """Fill in what came back for a request that `_issue` logged."""
        entry.status = int(httpx2.codes.OK)
        entry.duration_ms = round((self._clock() - started) * 1000)
        entry.total_count = total
        entry.records_returned = len(records)
        entry.is_cached = is_cached
        _log.info("upstream_request", **entry.model_dump())

    # --- one request, with its retries -------------------------------------------------------------

    async def _get_json(self, url: str) -> JsonObject:
        try:
            return await self._retrying(answers.wait_before_retry, self._attempt, url)
        except answers.Transient as failure:
            raise failure.as_service_error() from None

    async def _attempt(self, url: str) -> JsonObject:
        self._restore_after_quiet_period()
        async with self._slots:
            await self._bucket.take()
            try:
                response = await self._http.get(url)
            except httpx2.TimeoutException as error:
                raise answers.transient(url, "timeout") from error
            except httpx2.RequestError as error:
                raise answers.transient(url, "unavailable") from error
        return self._read(response, url)

    def _read(self, response: httpx2.Response, url: str) -> JsonObject:
        status = response.status_code
        if status == httpx2.codes.OK:
            try:
                body = response.json()
            except ValueError as error:
                raise answers.transient(url, "unavailable", status=status) from error
            if not isinstance(body, dict):
                raise answers.transient(url, "unavailable", status=status)
            return body
        if status in (httpx2.codes.FORBIDDEN, httpx2.codes.TOO_MANY_REQUESTS):
            # The edge answers 403 with an HTML page; no error body is ever read as JSON.
            self._note_throttled()
            raise answers.transient(
                url, "throttled", status=status, retry_after_s=answers.retry_after(response)
            )
        if status >= httpx2.codes.INTERNAL_SERVER_ERROR:
            raise answers.transient(url, "unavailable", status=status)
        # Any other answer, 400 and 414 included, means that a URL this service built is wrong. That is
        # a bug here: it is neither retried nor explained to the client.
        _log.error("upstream_rejected_query", url=url, status=status, upstream_message=response.text[:300])
        raise UpstreamRejectedQuery("ClinicalTrials.gov rejected a query that this service built.")

    def _note_throttled(self) -> None:
        self._concurrency = max(1, self._concurrency // 2)
        self._slots.total_tokens = self._concurrency
        self._throttled_until = self._clock() + _THROTTLE_MEMORY_S
        _log.warning("upstream_throttled", concurrency=self._concurrency)

    def _restore_after_quiet_period(self) -> None:
        if self._throttled_until is not None and self._clock() >= self._throttled_until:
            self._throttled_until = None
            self._concurrency = self._max_concurrency
            self._slots.total_tokens = self._concurrency


def _first_failure(failures: ExceptionGroup[Exception]) -> Exception:
    """The first plain exception inside a group, so a registry error keeps its type and its HTTP status."""
    first = failures.exceptions[0]
    return _first_failure(first) if isinstance(first, ExceptionGroup) else first


def _issue(ctx: RequestLog, url: str, origin: Origin) -> UpstreamRequest:
    """Log a request about to be made; `CtGovClient._complete` fills in what came back."""
    entry = UpstreamRequest(
        url=url,
        status=0,  # no answer yet
        duration_ms=0,
        total_count=None,
        records_returned=None,
        is_cached=False,
        origin=origin,
    )
    ctx.log_request(entry)
    return entry


def _version_of(body: JsonObject) -> ApiVersion:
    api_version, data_timestamp = body.get("apiVersion"), body.get("dataTimestamp")
    if not isinstance(api_version, str) or not isinstance(data_timestamp, str):
        raise answers.unreadable()
    return ApiVersion(api_version=api_version, data_timestamp=data_timestamp)
