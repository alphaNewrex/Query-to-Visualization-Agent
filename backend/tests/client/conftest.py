"""A scripted ClinicalTrials.gov for the client tests: `httpx2.MockTransport`, so no socket is opened.

respx and pytest-httpx patch httpx and do not intercept httpx2, so the mock sits in the transport.
"""

from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field

import httpx2
import pytest
import stamina

from ctviz.contract.response import UpstreamRequest
from ctviz.ctgov.client import CtGovClient
from ctviz.ctgov.study import Study
from ctviz.settings import Settings

Handler = Callable[[httpx2.Request], httpx2.Response | Awaitable[httpx2.Response]]

BASE = "https://registry.test/api/v2"
VERSION_BODY = {"apiVersion": "2.0.5", "dataTimestamp": "2026-10-06T09:00:05"}


def study_record(number: int) -> dict[str, object]:
    return {"protocolSection": {"identificationModule": {"nctId": f"NCT{number:08d}"}}}


def studies_body(
    numbers: Sequence[int], *, total: int | None = None, token: str | None = None
) -> dict[str, object]:
    body: dict[str, object] = {"studies": [study_record(number) for number in numbers]}
    if total is not None:
        body["totalCount"] = total
    if token is not None:
        body["nextPageToken"] = token
    return body


@dataclass
class FakeContext:
    """Stands in for the request context: it keeps what the client reports."""

    requests: list[UpstreamRequest] = field(default_factory=list)
    studies: list[Study] = field(default_factory=list)

    def log_request(self, entry: UpstreamRequest) -> None:
        self.requests.append(entry)

    def note_studies(self, studies: Iterable[Study]) -> None:
        self.studies.extend(studies)


@dataclass
class Registry:
    """Answers `/version` itself and every other path with `respond`; keeps the requests it saw."""

    respond: Handler
    requests: list[httpx2.Request] = field(default_factory=list)
    data_timestamp: str = VERSION_BODY["dataTimestamp"]

    async def __call__(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/version"):
            return httpx2.Response(200, json={**VERSION_BODY, "dataTimestamp": self.data_timestamp})
        self.requests.append(request)
        answer = self.respond(request)
        return await answer if isinstance(answer, Awaitable) else answer


@pytest.fixture(autouse=True)
def no_backoff() -> Iterator[None]:
    """Three attempts, as in production, without the waiting between them."""
    with stamina.set_testing(True, attempts=3):
        yield


@pytest.fixture
def ctx() -> FakeContext:
    return FakeContext()


@pytest.fixture
def settings() -> Settings:
    # A burst larger than any test needs keeps the rate gate out of the way, except where a test is about it.
    return Settings(ctgov_base_url=BASE, ctgov_burst=1000, ctgov_rate_per_s=1000.0)


@pytest.fixture
def registry() -> Registry:
    """The scripted registry; a test sets `registry.respond` before it calls the client."""
    return Registry(respond=lambda request: httpx2.Response(404, text=f"nothing scripted for {request.url}"))


@pytest.fixture
async def client(settings: Settings, registry: Registry) -> AsyncIterator[CtGovClient]:
    ctgov = CtGovClient(settings, transport=httpx2.MockTransport(registry))
    yield ctgov
    await ctgov.aclose()
