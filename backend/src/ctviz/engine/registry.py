"""The part of the ClinicalTrials.gov client that the engine uses.

The engine depends on this shape and not on the client class, so a test can answer from a list of trials
and the engine never learns whether a request went out.
"""

from collections.abc import Sequence
from typing import Protocol

from ctviz.contract.response import Origin
from ctviz.ctgov.client import Page, RequestLog, WalkResult
from ctviz.ctgov.params import Params


class Registry(Protocol):
    async def registry_size(self) -> int: ...

    async def count(self, params: Params, ctx: RequestLog, *, origin: Origin) -> int: ...

    async def sample(
        self,
        params: Params,
        ctx: RequestLog,
        *,
        fields: Sequence[str],
        page_size: int,
        sort: str | None,
        origin: Origin,
    ) -> Page: ...

    async def sorted_page(
        self, params: Params, ctx: RequestLog, *, fields: Sequence[str], sort: str, page_size: int
    ) -> Page: ...

    async def walk(
        self, params: Params, ctx: RequestLog, *, fields: Sequence[str], limit: int, sort: str | None = None
    ) -> WalkResult: ...
