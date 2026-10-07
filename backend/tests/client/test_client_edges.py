"""The client's edges: time, the order of the log, shared failures, and what a cache hit still owes.

These run on a clock the test moves by hand, so no test waits for a time to live to run out.
"""

from collections.abc import AsyncIterator

import anyio
import httpx2
import pytest

from ctviz.ctgov.client import CtGovClient
from ctviz.ctgov.params import Params
from ctviz.ctgov.ratelimit import TokenBucket
from ctviz.errors import UpstreamRateLimited, UpstreamUnavailable
from ctviz.settings import Settings
from tests.client.conftest import FakeContext, Registry, studies_body

pytestmark = pytest.mark.anyio

PEMBROLIZUMAB = Params((("query.intr", "pembrolizumab"),))


class FakeClock:
    """A clock that moves only when the test says so, or when something sleeps on it."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
async def timed(settings: Settings, registry: Registry, clock: FakeClock) -> AsyncIterator[CtGovClient]:
    ctgov = CtGovClient(settings, transport=httpx2.MockTransport(registry), clock=clock, sleep=clock.sleep)
    yield ctgov
    await ctgov.aclose()


def answering(total: int) -> httpx2.Response:
    return httpx2.Response(200, json=studies_body([1], total=total))


# --- time --------------------------------------------------------------------------------------------


async def test_an_answer_is_forgotten_when_its_time_to_live_runs_out(
    timed: CtGovClient, registry: Registry, settings: Settings, clock: FakeClock, ctx: FakeContext
) -> None:
    registry.respond = lambda request: answering(7)

    await timed.count(PEMBROLIZUMAB, ctx, origin="probe")
    clock.now += settings.cache_ttl_s - 1
    await timed.count(PEMBROLIZUMAB, ctx, origin="probe")
    assert len(registry.requests) == 1

    clock.now += 2
    await timed.count(PEMBROLIZUMAB, ctx, origin="probe")
    assert len(registry.requests) == 2


async def test_the_version_is_asked_again_after_five_minutes(
    settings: Settings, registry: Registry, clock: FakeClock, ctx: FakeContext
) -> None:
    paths: list[str] = []

    async def watching(request: httpx2.Request) -> httpx2.Response:
        paths.append(request.url.path)
        return await registry(request)

    registry.respond = lambda request: answering(7)
    ctgov = CtGovClient(settings, transport=httpx2.MockTransport(watching), clock=clock, sleep=clock.sleep)

    await ctgov.version()
    clock.now += 299
    await ctgov.version()
    assert paths.count("/api/v2/version") == 1

    clock.now += 2
    await ctgov.version()
    assert paths.count("/api/v2/version") == 2
    await ctgov.aclose()


async def test_a_429_that_clears_is_retried_and_the_registry_is_remembered_as_touchy(
    timed: CtGovClient, registry: Registry, clock: FakeClock, ctx: FakeContext
) -> None:
    answers = iter([httpx2.Response(429, text="slow down", headers={"Retry-After": "1"}), answering(4)])
    registry.respond = lambda request: next(answers)

    assert await timed.count(PEMBROLIZUMAB, ctx, origin="probe") == 4
    assert len(registry.requests) == 2
    assert timed.is_throttled

    clock.now += 599
    assert timed.is_throttled
    clock.now += 2
    assert not timed.is_throttled


async def test_concurrency_comes_back_after_ten_quiet_minutes(
    timed: CtGovClient, registry: Registry, clock: FakeClock, ctx: FakeContext
) -> None:
    registry.respond = lambda request: httpx2.Response(429, text="slow down")
    with pytest.raises(UpstreamRateLimited) as caught:
        await timed.count(PEMBROLIZUMAB, ctx, origin="probe")
    assert caught.value.headers == {"Retry-After": "10"}  # the registry named no wait
    assert timed._slots.total_tokens == 1

    registry.respond = lambda request: answering(4)
    clock.now += 601
    await timed.count(PEMBROLIZUMAB, ctx, origin="probe")

    assert timed._slots.total_tokens == 4


# --- the request log --------------------------------------------------------------------------------


async def test_the_log_keeps_the_order_of_issue_when_answers_arrive_out_of_order(
    client: CtGovClient, registry: Registry, ctx: FakeContext
) -> None:
    async def respond(request: httpx2.Request) -> httpx2.Response:
        if request.url.params["query.intr"] == "first":
            await anyio.sleep(0.05)  # the first request is the last to be answered
        return answering(1)

    registry.respond = respond

    async def ask(drug: str) -> None:
        await client.count(Params((("query.intr", drug),)), ctx, origin="probe")

    async with anyio.create_task_group() as group:
        for drug in ["first", "second", "third"]:
            group.start_soon(ask, drug)

    assert [entry.url.split("query.intr=")[1].split("&")[0] for entry in ctx.requests] == [
        "first",
        "second",
        "third",
    ]
    assert all(entry.status == 200 and entry.total_count == 1 for entry in ctx.requests)


async def test_requests_go_out_under_their_canonical_url_with_an_honest_user_agent(
    client: CtGovClient, registry: Registry, ctx: FakeContext
) -> None:
    registry.respond = lambda request: answering(2)

    await client.count(PEMBROLIZUMAB, ctx, origin="probe")

    sent = registry.requests[0]
    assert str(sent.url) == ctx.requests[0].url
    assert sent.headers["User-Agent"].startswith("ctviz/")
    assert sent.headers["Accept"] == "application/json"


# --- what a cache hit still owes the request -----------------------------------------------------------


async def test_a_cached_sample_still_hands_its_trials_to_the_next_request(
    client: CtGovClient, registry: Registry, ctx: FakeContext
) -> None:
    registry.respond = lambda request: httpx2.Response(200, json=studies_body([5, 6], total=9))
    later = FakeContext()

    first = await client.sample(
        PEMBROLIZUMAB, ctx, fields=["Phase"], page_size=2, sort=None, origin="execution"
    )
    again = await client.sample(
        PEMBROLIZUMAB, later, fields=["Phase"], page_size=2, sort=None, origin="execution"
    )

    assert len(registry.requests) == 1
    assert again == first
    assert [study.nct_id for study in later.studies] == ["NCT00000005", "NCT00000006"]
    assert [entry.is_cached for entry in later.requests] == [True]


async def test_a_count_keeps_none_of_its_records(
    client: CtGovClient, registry: Registry, ctx: FakeContext
) -> None:
    registry.respond = lambda request: answering(2971)

    await client.count(PEMBROLIZUMAB, ctx, origin="probe")

    assert ctx.studies == []


# --- sharing --------------------------------------------------------------------------------------------


async def test_a_failure_is_shared_by_every_caller_waiting_for_it(
    client: CtGovClient, registry: Registry, ctx: FakeContext
) -> None:
    async def respond(request: httpx2.Request) -> httpx2.Response:
        await anyio.sleep(0.05)  # all four callers are waiting by the time the first answer comes
        return httpx2.Response(503, text="busy")

    registry.respond = respond
    failures: list[Exception] = []

    async def ask() -> None:
        try:
            await client.count(PEMBROLIZUMAB, ctx, origin="probe")
        except UpstreamUnavailable as error:
            failures.append(error)

    async with anyio.create_task_group() as group:
        for _ in range(4):
            group.start_soon(ask)

    assert len(failures) == 4
    assert len(registry.requests) == 3  # one caller's three attempts, not twelve


async def test_a_caller_that_is_cancelled_does_not_fail_the_callers_waiting_with_it(
    client: CtGovClient, registry: Registry, ctx: FakeContext
) -> None:
    calls = 0
    first_request_sent = anyio.Event()

    async def respond(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            first_request_sent.set()
            await anyio.sleep_forever()  # the first caller's request never comes back
        return answering(9)

    registry.respond = respond
    leader_scope = anyio.CancelScope()
    answers: list[int] = []

    async def leader() -> None:
        with leader_scope:
            await client.count(PEMBROLIZUMAB, ctx, origin="probe")

    async def follower() -> None:
        answers.append(await client.count(PEMBROLIZUMAB, ctx, origin="probe"))

    async with anyio.create_task_group() as group:
        group.start_soon(leader)
        await first_request_sent.wait()
        group.start_soon(follower)
        await anyio.sleep(0.02)  # long enough for the follower to be waiting on the leader's fetch
        leader_scope.cancel()

    assert answers == [9]
    assert calls == 2


async def test_a_walk_says_when_the_registry_changed_under_it(
    client: CtGovClient, registry: Registry, ctx: FakeContext
) -> None:
    registry.respond = lambda request: httpx2.Response(200, json=studies_body([1, 2], total=3))

    result = await client.walk(PEMBROLIZUMAB, ctx, fields=["Phase"], limit=10)

    assert (len(result.studies), result.total, result.is_truncated) == (2, 3, False)
    assert not result.is_consistent


async def test_a_walk_that_read_everything_it_counted_is_consistent(
    client: CtGovClient, registry: Registry, ctx: FakeContext
) -> None:
    registry.respond = lambda request: httpx2.Response(200, json=studies_body([1, 2, 3], total=3))

    result = await client.walk(PEMBROLIZUMAB, ctx, fields=["Phase"], limit=10)

    assert result.is_consistent


# --- unreadable answers and bad calls ------------------------------------------------------------------


async def test_a_record_without_an_nct_id_is_an_unreadable_answer(
    client: CtGovClient, registry: Registry, ctx: FakeContext
) -> None:
    registry.respond = lambda request: httpx2.Response(
        200, json={"totalCount": 1, "studies": [{"protocolSection": {}}]}
    )

    with pytest.raises(UpstreamUnavailable):
        await client.sample(PEMBROLIZUMAB, ctx, fields=["Phase"], page_size=1, sort=None, origin="probe")
    assert len(registry.requests) == 1  # a malformed record is not a transient failure


async def test_a_walk_and_a_page_have_bounds(client: CtGovClient, ctx: FakeContext) -> None:
    with pytest.raises(ValueError, match="at least one trial"):
        await client.walk(PEMBROLIZUMAB, ctx, fields=["Phase"], limit=0)
    with pytest.raises(ValueError, match="page holds"):
        await client.sample(PEMBROLIZUMAB, ctx, fields=["Phase"], page_size=0, sort=None, origin="probe")


# --- the token bucket ----------------------------------------------------------------------------------


async def test_the_token_bucket_spaces_requests_after_its_burst(clock: FakeClock) -> None:
    bucket = TokenBucket(burst=3, rate_per_s=2.0, clock=clock, sleep=clock.sleep)

    for _ in range(3):
        await bucket.take()
    assert clock.sleeps == []

    await bucket.take()
    assert clock.sleeps == [0.5]

    clock.now += 60  # a long quiet time refills the bucket, but never beyond its burst
    for _ in range(3):
        await bucket.take()
    assert clock.sleeps == [0.5]
    await bucket.take()
    assert clock.sleeps == [0.5, 0.5]


@pytest.mark.parametrize(("burst", "rate"), [(0, 5.0), (10, 0.0), (10, -1.0)])
def test_a_token_bucket_needs_a_burst_and_a_rate(burst: int, rate: float, clock: FakeClock) -> None:
    with pytest.raises(ValueError, match="burst of at least 1 and a positive rate"):
        TokenBucket(burst=burst, rate_per_s=rate, clock=clock, sleep=clock.sleep)
