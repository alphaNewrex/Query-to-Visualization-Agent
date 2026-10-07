"""A long walk is split into disjoint ranges of first-posted date, each with a token chain of its own."""

import re
from collections.abc import Sequence
from datetime import date, timedelta

import anyio
import httpx2
import pytest

from ctviz.ctgov.client import CtGovClient
from ctviz.ctgov.params import Params
from ctviz.ctgov.partition import partition
from tests.client.conftest import FakeContext, Registry

pytestmark = pytest.mark.anyio

SEARCH = Params((("query.intr", "drug"),))
_RANGE = re.compile(r"RANGE\[([^,]*),([^\]]*)\]")


def record(number: int, posted: date) -> dict[str, object]:
    return {
        "protocolSection": {
            "identificationModule": {"nctId": f"NCT{number:08d}"},
            "statusModule": {"studyFirstPostDateStruct": {"date": posted.isoformat()}},
        }
    }


def dated_registry(posted: Sequence[date], *, drop_after: int | None = None) -> "Fake":
    return Fake(sorted(posted), drop_after)


class Fake:
    """Answers counts, ends and pages over trials with these first-posted dates, like the registry."""

    def __init__(self, posted: Sequence[date], drop_after: int | None) -> None:
        self.posted = list(posted)
        self.drop_after = drop_after  # a registry refresh: pages after this many requests lose a trial
        self.calls = 0
        self.running = self.peak = 0

    def _matching(self, request: httpx2.Request) -> list[tuple[int, date]]:
        expr = request.url.params.get("filter.advanced", "")
        found = _RANGE.search(expr)
        low = None if found is None or found.group(1) == "MIN" else date.fromisoformat(found.group(1))
        high = None if found is None or found.group(2) == "MAX" else date.fromisoformat(found.group(2))
        return [
            (number, day)
            for number, day in enumerate(self.posted, 1)
            if (low is None or day >= low) and (high is None or day <= high)
        ]

    async def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.calls += 1
        self.running += 1
        self.peak = max(self.peak, self.running)
        await anyio.sleep(0.001)
        self.running -= 1
        matching = self._matching(request)
        sort = request.url.params.get("sort")
        if sort == "StudyFirstPostDate:desc":
            matching = matching[::-1]
        size = int(request.url.params["pageSize"])
        start = int(request.url.params.get("pageToken", 0))
        chunk = matching[start : start + size]
        if self.drop_after is not None and self.calls > self.drop_after and size > 1:
            chunk = chunk[:-1]
        body: dict[str, object] = {"studies": [record(n, d) for n, d in chunk]}
        if start == 0:
            body["totalCount"] = len(matching)
        if chunk and start + size < len(matching):
            body["nextPageToken"] = str(start + size)
        return httpx2.Response(200, json=body)


def spread(count: int, *, per_day: int = 3) -> list[date]:
    first = date(2005, 1, 1)
    return [first + timedelta(days=index // per_day) for index in range(count)]


async def run(fake: Fake, registry: Registry, client: CtGovClient, ctx: FakeContext):  # type: ignore[no-untyped-def]
    async def respond(request: httpx2.Request) -> httpx2.Response:
        return await fake(request)

    registry.respond = respond
    return await client.walk(SEARCH, ctx, fields=["StartDate"])


async def test_a_long_walk_is_split_and_reads_every_trial_once(
    client: CtGovClient, registry: Registry, ctx: FakeContext
) -> None:
    fake = dated_registry(spread(9000))

    result = await run(fake, registry, client, ctx)

    assert result.total == 9000 and len(result.studies) == 9000 and result.is_consistent
    assert len({study.nct_id for study in result.studies}) == 9000
    ranges = [r for r in (request.url.params.get("filter.advanced") for request in registry.requests) if r]
    assert ranges, "the walk was split by first-posted date"
    # Only chains of pages walk with a token; no range holds more than the target of three pages.
    assert fake.peak <= 4  # the configured concurrency


async def test_ranges_are_open_at_both_ends_so_no_trial_can_fall_outside_them(
    client: CtGovClient, registry: Registry, ctx: FakeContext
) -> None:
    await run(dated_registry(spread(6000)), registry, client, ctx)

    pages = [
        request.url.params["filter.advanced"]
        for request in registry.requests
        if request.url.params.get("pageSize") == "1000" and "pageToken" not in request.url.params
    ]
    assert any("RANGE[MIN," in expr for expr in pages)
    assert any(",MAX]" in expr for expr in pages)


async def test_a_trial_that_vanished_during_a_split_walk_is_reported_as_inconsistent(
    client: CtGovClient, registry: Registry, ctx: FakeContext
) -> None:
    fake = dated_registry(spread(6000), drop_after=8)

    result = await run(fake, registry, client, ctx)

    assert result.total == 6000 and len(result.studies) < 6000
    assert not result.is_consistent


async def test_a_split_walk_is_cached_whole(
    client: CtGovClient, registry: Registry, ctx: FakeContext
) -> None:
    fake = dated_registry(spread(6000))
    await run(fake, registry, client, ctx)
    sent = len(registry.requests)

    again = await client.walk(SEARCH, ctx, fields=["StartDate"])

    assert len(registry.requests) == sent and len(again.studies) == 6000
    assert ctx.requests[-1].is_cached


async def test_trials_that_share_one_first_posted_day_are_one_unsplit_range() -> None:
    async def count(_: Params) -> int:
        raise AssertionError("a single day cannot be cut, so nothing is counted")

    day = date(1999, 11, 2)

    parts = await partition(SEARCH, 5000, day, day, leaf_max=1000, count=count)

    assert [(part.params, part.expected) for part in parts] == [(SEARCH, 5000)]


async def test_a_long_walk_of_trials_posted_on_one_day_reads_them_all(
    client: CtGovClient, registry: Registry, ctx: FakeContext
) -> None:
    fake = dated_registry([date(2010, 5, 5)] * 2500)

    result = await run(fake, registry, client, ctx)

    assert result.total == 2500 and len(result.studies) == 2500 and result.is_consistent
