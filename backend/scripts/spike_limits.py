"""Live checks of how much load ClinicalTrials.gov tolerates and of whether its relevance samples repeat.

Milestone M0 of docs/PLAN.md (sections 4.9 and 4.13). About 67 requests go to the registry:

1. The relevance-ordered citation sample of one bucket, requested twice. Fan-out citations
   depend on the same trials coming back in the same order, with the same total.
2. The ramped burst test: batches of 5, 10, 20 and 30 count calls at the service's concurrency
   with no rate gate. By the rule of 4.9 its outcome sets the limiter defaults of `ctviz.settings`.

Run from backend/:  uv run python scripts/spike_limits.py [--raw-output results.json]
The tables are printed as Markdown; the raw per-call results go to the JSON file.
"""

import argparse
import calendar
import json
import statistics
import time
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path
from typing import Final

import anyio
import httpx2

from ctviz.settings import Settings

# The defaults the service ships with. Taken from the class and not from `Settings()`, so that no
# environment variable and no env file (the real one holds a key) is read.
DEFAULTS: Final = Settings.model_construct()

USER_AGENT: Final = (
    "ctviz-spike-limits/0.1 (ClinicalTrials.gov query-to-visualization take-home; one-off burst test)"
)
BATCH_SIZES: Final = (5, 10, 20, 30)
# Long enough for a per-second or per-ten-seconds allowance to refill, so that a throttled batch
# is blamed on its own size and not on the batches before it.
PAUSE_BETWEEN_BATCHES_S: Final = 10.0
THROTTLE_STATUSES: Final = frozenset({HTTPStatus.FORBIDDEN, HTTPStatus.TOO_MANY_REQUESTS})
NO_RESPONSE: Final = 0  # the status recorded when a timeout or connection error means none arrived

# The citation sample of the 2015 pembrolizumab bucket as 5.4 writes it: five trials (the default
# citation cap) in the registry's own relevance order.
RELEVANCE_SAMPLE_PARAMS: Final = {
    "query.intr": "pembrolizumab",
    "filter.advanced": (
        "(AREA[StartDate]RANGE[2015-01-01,MAX]) AND (AREA[StartDate]RANGE[2015-01-01,2015-12-31])"
    ),
    "countTotal": "true",
    "pageSize": "5",
    "fields": (
        "NCTId,BriefTitle,StudyFirstPostDate,StartDate,StartDateType,InterventionName,InterventionOtherName"
    ),
    "sort": "@relevance",
}
FIRST_BUCKET_YEAR: Final = 2021


@dataclass(frozen=True)
class Call:
    """One count call: the bucket it asked for, the status it got and how long it took."""

    bucket: str
    status: int
    seconds: float


@dataclass(frozen=True)
class Batch:
    """The calls of one step of the ramp, sent together."""

    wall_s: float
    calls: tuple[Call, ...]

    @property
    def size(self) -> int:
        return len(self.calls)

    @property
    def is_clean(self) -> bool:
        return all(call.status == HTTPStatus.OK for call in self.calls)

    @property
    def is_throttled(self) -> bool:
        return any(call.status in THROTTLE_STATUSES for call in self.calls)

    @property
    def median_s(self) -> float:
        return statistics.median(call.seconds for call in self.calls)

    @property
    def max_s(self) -> float:
        return max(call.seconds for call in self.calls)


@dataclass(frozen=True)
class Sample:
    """One response to the relevance-ordered sample request."""

    seconds: float
    total_count: int
    nct_ids: tuple[str, ...]
    # The ETag encodes the data timestamp, so equal ETags rule out a refresh between two requests.
    etag: str | None


@dataclass(frozen=True)
class Limits:
    """What the burst test decides (4.9), and the rule branch that decided it."""

    burst: int
    rate_per_s: float
    one_page_max: int
    reason: str


@dataclass(frozen=True)
class SpikeResult:
    started_at: str
    concurrency: int
    pause_between_batches_s: float
    relevance_samples: tuple[Sample, Sample]
    batches: tuple[Batch, ...]
    limits: Limits


def count_params(bucket: str) -> dict[str, str]:
    """The count primitive of 4.9 for one date bucket, the form of one request of a fan-out."""
    return {
        "query.intr": "pembrolizumab",
        "filter.advanced": f"AREA[StartDate]RANGE[{bucket}]",
        "countTotal": "true",
        "pageSize": "1",  # 0 would return ten rows, so 1 is the least
        "fields": "NCTId",
    }


def month_bucket(index: int) -> str:
    """The `index`-th month from January of `FIRST_BUCKET_YEAR` as a date range.

    Every call of the test asks for a different month, so no response can be a repeat of an
    earlier one that a cache might have answered.
    """
    year, month = FIRST_BUCKET_YEAR + index // 12, 1 + index % 12
    return f"{year}-{month:02d}-01,{year}-{month:02d}-{calendar.monthrange(year, month)[1]}"


async def fetch_relevance_sample(client: httpx2.AsyncClient) -> Sample:
    started = time.perf_counter()
    response = await client.get("/studies", params=RELEVANCE_SAMPLE_PARAMS)
    seconds = time.perf_counter() - started
    response.raise_for_status()
    payload = response.json()
    return Sample(
        seconds=seconds,
        total_count=payload["totalCount"],
        nct_ids=tuple(
            study["protocolSection"]["identificationModule"]["nctId"] for study in payload["studies"]
        ),
        etag=response.headers.get("etag"),
    )


async def timed_count(client: httpx2.AsyncClient, bucket: str) -> Call:
    started = time.perf_counter()
    try:
        status = (await client.get("/studies", params=count_params(bucket))).status_code
    except httpx2.TransportError:
        status = NO_RESPONSE
    return Call(bucket=bucket, status=status, seconds=time.perf_counter() - started)


async def run_batch(client: httpx2.AsyncClient, buckets: Sequence[str]) -> Batch:
    """Send one count call per bucket, `ctgov_concurrency` at a time and with no rate gate."""
    limiter = anyio.CapacityLimiter(DEFAULTS.ctgov_concurrency)
    calls: list[Call] = []

    async def send(bucket: str) -> None:
        async with limiter:
            calls.append(await timed_count(client, bucket))

    started = time.perf_counter()
    async with anyio.create_task_group() as tasks:
        for bucket in buckets:
            tasks.start_soon(send, bucket)
    return Batch(wall_s=time.perf_counter() - started, calls=tuple(calls))


async def run_burst_test(client: httpx2.AsyncClient) -> tuple[Batch, ...]:
    """Send ever larger batches, stopping after the first one that held a 429 or a 403.

    A batch is always sent whole: its mix of statuses shows how the registry throttles.
    """
    batches: list[Batch] = []
    sent = 0
    for size in BATCH_SIZES:
        if batches:
            await anyio.sleep(PAUSE_BETWEEN_BATCHES_S)
        batches.append(await run_batch(client, [month_bucket(sent + offset) for offset in range(size)]))
        sent += size
        if batches[-1].is_throttled:
            break
    return tuple(batches)


def limits_from(batches: Sequence[Batch]) -> Limits:
    """The rule of 4.9.

    A call that failed without a 429 or 403 leaves the test inconclusive, and the defaults stay.
    """
    throttled_at = next((index for index, batch in enumerate(batches) if batch.is_throttled), None)
    if throttled_at is not None:
        last_clean = max((batch.size for batch in batches[:throttled_at] if batch.is_clean), default=0)
        return Limits(
            # A burst of zero would never let a request out, so one is the least.
            burst=max(1, last_clean // 2),
            rate_per_s=2.0,
            # Walks are preferred when fan-outs are slow.
            one_page_max=DEFAULTS.one_page_max,
            reason=f"throttled at batch {throttled_at + 1} ({batches[throttled_at].size} calls)",
        )
    if all(batch.is_clean for batch in batches):
        return Limits(
            burst=20,
            rate_per_s=10.0,
            one_page_max=DEFAULTS.one_page_max,
            reason=f"clean through {batches[-1].size} calls",
        )
    return Limits(
        burst=DEFAULTS.ctgov_burst,
        rate_per_s=DEFAULTS.ctgov_rate_per_s,
        one_page_max=DEFAULTS.one_page_max,
        reason="inconclusive, a call failed without a 429 or 403",
    )


async def run_checks(client: httpx2.AsyncClient) -> SpikeResult:
    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    # The two cheap requests come first: an unreachable registry or a wrong request form shows up
    # before the 65 of the burst test are spent.
    relevance_samples = (await fetch_relevance_sample(client), await fetch_relevance_sample(client))
    batches = await run_burst_test(client)
    return SpikeResult(
        started_at=started_at,
        concurrency=DEFAULTS.ctgov_concurrency,
        pause_between_batches_s=PAUSE_BETWEEN_BATCHES_S,
        relevance_samples=relevance_samples,
        batches=batches,
        limits=limits_from(batches),
    )


async def measure() -> SpikeResult:
    """Run both checks over one pooled client, configured as the service's client will be."""
    async with httpx2.AsyncClient(
        base_url=DEFAULTS.ctgov_base_url,
        headers={"User-Agent": USER_AGENT},
        timeout=httpx2.Timeout(20.0, connect=5.0),
    ) as client:
        return await run_checks(client)


def markdown_table(header: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    lines = [f"| {' | '.join(header)} |", f"| {' | '.join('---' for _ in header)} |"]
    lines.extend(f"| {' | '.join(str(cell) for cell in row)} |" for row in rows)
    return "\n".join(lines)


def describe_statuses(batch: Batch) -> str:
    counts = Counter(call.status for call in batch.calls)
    return ", ".join(
        f"{'no response' if status == NO_RESPONSE else status} x {count}"
        for status, count in sorted(counts.items())
    )


def render_relevance(samples: tuple[Sample, Sample]) -> str:
    first, second = samples
    rows = [
        (number, sample.total_count, ", ".join(sample.nct_ids), f"{sample.seconds:.3f}", f"`{sample.etag}`")
        for number, sample in enumerate(samples, start=1)
    ]
    table = markdown_table(("Request", "totalCount", "Trials in the order returned", "Seconds", "ETag"), rows)
    same_total = "yes" if first.total_count == second.total_count else "NO"
    same_order = "yes" if first.nct_ids == second.nct_ids else "NO"
    return (
        f"### Relevance-ordered sample, requested twice\n\n{table}\n\n"
        f"Same total: {same_total}. Same trials in the same order: {same_order}."
    )


def render_burst(batches: Sequence[Batch]) -> str:
    rows = [
        (
            number,
            batch.size,
            describe_statuses(batch),
            f"{batch.wall_s:.2f}",
            f"{batch.median_s:.3f}",
            f"{batch.max_s:.3f}",
        )
        for number, batch in enumerate(batches, start=1)
    ]
    header = ("Batch", "Calls", "Statuses", "Wall time (s)", "Median latency (s)", "Max latency (s)")
    return f"### Ramped burst test\n\n{markdown_table(header, rows)}"


def render_limits(limits: Limits) -> str:
    rows = [
        ("ctgov_burst", DEFAULTS.ctgov_burst, limits.burst),
        ("ctgov_rate_per_s", DEFAULTS.ctgov_rate_per_s, limits.rate_per_s),
        ("one_page_max", DEFAULTS.one_page_max, limits.one_page_max),
    ]
    table = markdown_table(("Setting", "In settings.py", "By the rule of 4.9"), rows)
    return f"### Limiter values: {limits.reason}\n\n{table}"


def render(result: SpikeResult) -> str:
    requests_sent = len(result.relevance_samples) + sum(batch.size for batch in result.batches)
    return "\n\n".join(
        [
            render_relevance(result.relevance_samples),
            render_burst(result.batches),
            render_limits(result.limits),
            f"Run at {result.started_at}; {requests_sent} requests sent to the registry.",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Burst test and relevance-sample check against ClinicalTrials.gov."
    )
    parser.add_argument("--raw-output", type=Path, help="also write the raw results to this JSON file")
    arguments = parser.parse_args()

    result = anyio.run(measure)
    print(render(result))
    if arguments.raw_output is not None:
        arguments.raw_output.parent.mkdir(parents=True, exist_ok=True)
        arguments.raw_output.write_text(json.dumps(asdict(result), indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
