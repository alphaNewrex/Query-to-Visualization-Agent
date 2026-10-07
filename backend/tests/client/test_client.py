"""The client over a scripted registry: counts, samples, walks, the cache and the failures."""

from collections.abc import Callable

import anyio
import httpx2
import pytest

from ctviz.ctgov import essie
from ctviz.ctgov.client import CtGovClient
from ctviz.ctgov.params import Params
from ctviz.errors import (
    AppError,
    UpstreamRateLimited,
    UpstreamTimeout,
    UpstreamUnavailable,
)
from ctviz.settings import Settings
from tests.client.conftest import BASE, FakeContext, Registry, studies_body

pytestmark = pytest.mark.anyio

PEMBROLIZUMAB = Params((("query.intr", "pembrolizumab"),))


def query_of(request: httpx2.Request) -> dict[str, str]:
    return dict(request.url.params.items())


# --- version and registry size -----------------------------------------------------------------------


async def test_the_version_is_asked_once_and_remembered(client: CtGovClient, registry: Registry) -> None:
    first = await client.version()
    second = await client.version()

    assert first == second
    assert (first.api_version, first.data_timestamp) == ("2.0.5", "2026-10-06T09:00:05")


async def test_the_registry_size_is_the_unfiltered_count_and_is_counted_once(
    client: CtGovClient, registry: Registry
) -> None:
    registry.respond = lambda request: httpx2.Response(200, json=studies_body([1], total=606007))

    assert await client.registry_size() == 606007
    assert await client.registry_size() == 606007
    assert len(registry.requests) == 1


# --- count and sample --------------------------------------------------------------------------------


async def test_a_count_asks_for_one_record_and_the_total(
    client: CtGovClient, registry: Registry, ctx: FakeContext
) -> None:
    registry.respond = lambda request: httpx2.Response(200, json=studies_body([1], total=2971))

    assert await client.count(PEMBROLIZUMAB, ctx, origin="probe") == 2971

    sent = query_of(registry.requests[0])
    assert sent == {"query.intr": "pembrolizumab", "countTotal": "true", "pageSize": "1", "fields": "NCTId"}
    logged = [(call.origin, call.total_count, call.is_cached) for call in ctx.requests]
    assert logged == [("probe", 2971, False)]


async def test_a_repeated_count_comes_from_the_cache_and_is_still_logged(
    client: CtGovClient, registry: Registry, ctx: FakeContext
) -> None:
    registry.respond = lambda request: httpx2.Response(200, json=studies_body([1], total=7))

    await client.count(PEMBROLIZUMAB, ctx, origin="resolution")
    await client.count(PEMBROLIZUMAB, ctx, origin="execution")

    assert len(registry.requests) == 1
    logged = [(call.origin, call.is_cached) for call in ctx.requests]
    assert logged == [("resolution", False), ("execution", True)]


async def test_identical_requests_in_flight_share_one_upstream_request(
    client: CtGovClient, registry: Registry, ctx: FakeContext
) -> None:
    async def slow(request: httpx2.Request) -> httpx2.Response:
        await anyio.sleep(0.05)
        return httpx2.Response(200, json=studies_body([1], total=5))

    registry.respond = slow

    async def ask() -> None:
        await client.count(PEMBROLIZUMAB, ctx, origin="probe")

    async with anyio.create_task_group() as group:
        for _ in range(5):
            group.start_soon(ask)

    assert len(registry.requests) == 1
    assert len(ctx.requests) == 5


async def test_a_new_data_timestamp_stops_old_answers_from_matching(
    client: CtGovClient, registry: Registry, ctx: FakeContext
) -> None:
    totals = iter([7, 8])
    registry.respond = lambda request: httpx2.Response(200, json=studies_body([1], total=next(totals)))
    assert await client.count(PEMBROLIZUMAB, ctx, origin="probe") == 7

    registry.data_timestamp = "2026-10-07T09:00:00"
    client._version_expires = 0.0  # the five minutes of the cached version ran out

    assert await client.count(PEMBROLIZUMAB, ctx, origin="probe") == 8


async def test_a_sample_returns_the_total_and_the_records_with_a_canonical_url(
    client: CtGovClient, registry: Registry, ctx: FakeContext
) -> None:
    registry.respond = lambda request: httpx2.Response(200, json=studies_body([5, 6, 7], total=120))
    params = PEMBROLIZUMAB.narrowed_by(essie.range_("StartDate", "2015-01-01", "2015-12-31"))

    page = await client.sample(
        params, ctx, fields=["BriefTitle", "StartDate"], page_size=3, sort="@relevance", origin="execution"
    )

    assert page.total == 120
    assert [study.nct_id for study in page.studies] == ["NCT00000005", "NCT00000006", "NCT00000007"]
    assert page.url == str(registry.requests[0].url)
    sent = query_of(registry.requests[0])
    assert sent["fields"] == "NCTId,BriefTitle,StartDate"
    assert sent["sort"] == "@relevance"
    assert sent["filter.advanced"] == "AREA[StartDate]RANGE[2015-01-01,2015-12-31]"
    assert [study.nct_id for study in ctx.studies] == ["NCT00000005", "NCT00000006", "NCT00000007"]


async def test_a_page_size_the_registry_would_clamp_is_refused(client: CtGovClient, ctx: FakeContext) -> None:
    with pytest.raises(ValueError, match="page holds"):
        await client.sample(PEMBROLIZUMAB, ctx, fields=[], page_size=1001, sort=None, origin="probe")


async def test_a_sorted_page_sends_the_sort(
    client: CtGovClient, registry: Registry, ctx: FakeContext
) -> None:
    registry.respond = lambda request: httpx2.Response(200, json=studies_body([1, 2], total=2))

    page = await client.sorted_page(
        PEMBROLIZUMAB, ctx, fields=["StartDate"], sort="StartDate:desc", page_size=10
    )

    assert page.total == 2
    assert query_of(registry.requests[0])["sort"] == "StartDate:desc"
    assert ctx.requests[0].origin == "execution"


# --- walks -------------------------------------------------------------------------------------------


def pages_of(total: int, size: int = 1000) -> Callable[[httpx2.Request], httpx2.Response]:
    """A registry of `total` trials that serves `size` per page; the token is the offset of the next page.

    Like the real one, it sends `totalCount` on page one only, and a token even after a full last page.
    """

    def respond(request: httpx2.Request) -> httpx2.Response:
        query = query_of(request)
        start = int(query.get("pageToken", 0))
        numbers = list(range(start + 1, min(start + size, total) + 1))
        body = studies_body(numbers, total=total if start == 0 else None)
        if numbers and start + size <= total:
            body["nextPageToken"] = str(start + size)
        return httpx2.Response(200, json=body)

    return respond


async def test_a_walk_follows_tokens_with_the_same_parameters(
    client: CtGovClient, registry: Registry, ctx: FakeContext
) -> None:
    registry.respond = pages_of(2500)

    result = await client.walk(PEMBROLIZUMAB, ctx, fields=["Phase", "StartDate"], limit=5000)

    assert len(result.studies) == 2500
    assert (result.total, result.is_truncated) == (2500, False)
    assert [query_of(request).get("pageToken") for request in registry.requests] == [None, "1000", "2000"]
    shared = query_of(registry.requests[0])
    assert shared["pageSize"] == "1000"
    for request in registry.requests[1:]:
        assert {k: v for k, v in query_of(request).items() if k != "pageToken"} == shared
    assert [call.total_count for call in ctx.requests] == [2500, None, None]


async def test_a_walk_with_every_counted_trial_does_not_ask_for_the_empty_page(
    client: CtGovClient, registry: Registry, ctx: FakeContext
) -> None:
    registry.respond = pages_of(2000)

    result = await client.walk(PEMBROLIZUMAB, ctx, fields=["Phase"], limit=5000)

    assert (len(result.studies), result.is_truncated) == (2000, False)
    assert len(registry.requests) == 2  # the full last page carries a token to an empty page; not followed


async def test_a_walk_stops_at_the_limit_and_says_it_is_truncated(
    client: CtGovClient, registry: Registry, ctx: FakeContext
) -> None:
    registry.respond = pages_of(9, size=4)

    result = await client.walk(PEMBROLIZUMAB, ctx, fields=["Phase"], limit=4, sort="StudyFirstPostDate:desc")

    assert [study.nct_id[-1] for study in result.studies] == ["1", "2", "3", "4"]
    assert (result.total, result.is_truncated) == (9, True)
    assert query_of(registry.requests[0])["pageSize"] == "4"
    assert len(registry.requests) == 1


async def test_a_trial_that_shows_on_two_pages_counts_once(
    client: CtGovClient, registry: Registry, ctx: FakeContext
) -> None:
    pages = iter(
        [
            httpx2.Response(200, json=studies_body(range(1, 1001), total=1001, token="T1")),
            httpx2.Response(200, json=studies_body([1000, 1001])),
        ]
    )
    registry.respond = lambda request: next(pages)

    result = await client.walk(PEMBROLIZUMAB, ctx, fields=["Phase"], limit=5000)

    assert len(result.studies) == 1001
    assert len({study.nct_id for study in result.studies}) == 1001


async def test_a_repeated_walk_is_served_from_the_cache(
    client: CtGovClient, registry: Registry, ctx: FakeContext
) -> None:
    registry.respond = pages_of(1500)

    await client.walk(PEMBROLIZUMAB, ctx, fields=["Phase"], limit=5000)
    again = await client.walk(PEMBROLIZUMAB, ctx, fields=["Phase"], limit=5000)

    assert len(registry.requests) == 2
    assert len(again.studies) == 1500
    assert [call.is_cached for call in ctx.requests] == [False, False, True]


# --- failures ----------------------------------------------------------------------------------------


async def test_a_503_that_clears_is_retried(
    client: CtGovClient, registry: Registry, ctx: FakeContext
) -> None:
    answers = iter([httpx2.Response(503, text="busy"), httpx2.Response(200, json=studies_body([1], total=4))])
    registry.respond = lambda request: next(answers)

    assert await client.count(PEMBROLIZUMAB, ctx, origin="probe") == 4
    assert len(registry.requests) == 2


async def test_a_5xx_that_persists_is_upstream_unavailable_after_three_attempts(
    client: CtGovClient, registry: Registry, ctx: FakeContext
) -> None:
    registry.respond = lambda request: httpx2.Response(502, text="<html>Bad gateway</html>")

    with pytest.raises(UpstreamUnavailable):
        await client.count(PEMBROLIZUMAB, ctx, origin="probe")
    assert len(registry.requests) == 3


@pytest.mark.parametrize(
    ("status", "body"),
    [(429, "slow down"), (403, "<html><body>Forbidden</body></html>")],
)
async def test_throttling_is_retried_then_reported_with_retry_after_and_remembered(
    client: CtGovClient, registry: Registry, ctx: FakeContext, status: int, body: str
) -> None:
    registry.respond = lambda request: httpx2.Response(status, text=body, headers={"Retry-After": "7"})

    with pytest.raises(UpstreamRateLimited) as caught:
        await client.count(PEMBROLIZUMAB, ctx, origin="probe")

    assert caught.value.headers == {"Retry-After": "7"}
    assert len(registry.requests) == 3
    assert client.is_throttled
    assert client._slots.total_tokens == 1  # 4 halved by each of three answers, never below one


async def test_a_retry_after_that_does_not_fit_the_deadline_is_not_waited_for(
    client: CtGovClient, registry: Registry
) -> None:
    registry.respond = lambda request: httpx2.Response(429, text="slow", headers={"Retry-After": "7"})

    with pytest.raises(UpstreamRateLimited), anyio.fail_after(3):
        await client.count(PEMBROLIZUMAB, FakeContext(), origin="probe")
    assert len(registry.requests) == 1


async def test_a_timeout_is_upstream_timeout_after_retries(
    client: CtGovClient, registry: Registry, ctx: FakeContext
) -> None:
    def hang(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow", request=request)

    registry.respond = hang

    with pytest.raises(UpstreamTimeout):
        await client.count(PEMBROLIZUMAB, ctx, origin="probe")
    assert len(registry.requests) == 3


async def test_a_refused_connection_is_upstream_unavailable(
    client: CtGovClient, registry: Registry, ctx: FakeContext
) -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    registry.respond = refuse

    with pytest.raises(UpstreamUnavailable):
        await client.count(PEMBROLIZUMAB, ctx, origin="probe")


@pytest.mark.parametrize("status", [400, 404, 414])
async def test_a_rejected_query_is_an_internal_error_and_is_not_retried(
    client: CtGovClient, registry: Registry, ctx: FakeContext, status: int
) -> None:
    registry.respond = lambda request: httpx2.Response(
        status, text="inner bool query clause cannot be null", headers={"Content-Type": "text/plain"}
    )

    with pytest.raises(AppError) as caught:
        await client.count(PEMBROLIZUMAB, ctx, origin="probe")

    assert caught.value.http_status == 500
    assert "inner bool" not in caught.value.message  # the provider's text is never sent to clients
    assert len(registry.requests) == 1


@pytest.mark.parametrize("body", ["<html>maintenance</html>", "[1, 2]", "{}"])
async def test_an_unreadable_ok_body_is_upstream_unavailable(
    client: CtGovClient, registry: Registry, ctx: FakeContext, body: str
) -> None:
    registry.respond = lambda request: httpx2.Response(200, text=body)

    with pytest.raises(UpstreamUnavailable):
        await client.count(PEMBROLIZUMAB, ctx, origin="probe")


# --- the limiter -------------------------------------------------------------------------------------


async def test_no_more_than_the_configured_number_of_requests_run_at_once(
    client: CtGovClient, registry: Registry, ctx: FakeContext
) -> None:
    running = peak = 0

    async def respond(request: httpx2.Request) -> httpx2.Response:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await anyio.sleep(0.02)
        running -= 1
        return httpx2.Response(200, json=studies_body([1], total=1))

    registry.respond = respond

    async def ask(number: int) -> None:
        await client.count(Params((("query.intr", f"drug{number}"),)), ctx, origin="probe")

    async with anyio.create_task_group() as group:
        for number in range(12):
            group.start_soon(ask, number)

    assert len(registry.requests) == 12
    assert peak == 4


async def test_the_token_bucket_allows_a_burst_then_spaces_the_rest(
    registry: Registry, ctx: FakeContext
) -> None:
    registry.respond = lambda request: httpx2.Response(200, json=studies_body([1], total=1))
    gated = Settings(ctgov_base_url=BASE, ctgov_burst=3, ctgov_rate_per_s=20.0)
    gated_client = CtGovClient(gated, transport=httpx2.MockTransport(registry))

    started = anyio.current_time()
    for number in range(2):  # with the version request, the three tokens of the burst
        await gated_client.count(Params((("query.intr", f"drug{number}"),)), ctx, origin="probe")
    burst_time = anyio.current_time() - started
    for number in range(2, 6):
        await gated_client.count(Params((("query.intr", f"drug{number}"),)), ctx, origin="probe")
    total_time = anyio.current_time() - started
    await gated_client.aclose()

    assert burst_time < 0.04
    assert total_time >= 4 / 20.0 - 0.02
