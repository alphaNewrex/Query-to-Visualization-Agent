"""A cache with a time to live whose misses are shared: identical requests that overlap make one fetch."""

from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass, field

import anyio
from cachetools import TTLCache


def _keep_all(_value: object) -> bool:
    return True


@dataclass
class _Flight[V]:
    """A fetch under way; callers that ask for the same key meanwhile wait on `done`."""

    done: anyio.Event = field(default_factory=anyio.Event)
    value: V | None = None
    error: Exception | None = None


class SingleFlightCache[V]:
    """Values by key for `ttl_s` seconds, at most `maxsize` of them. A value is never None."""

    def __init__(self, maxsize: int, ttl_s: float, clock: Callable[[], float]) -> None:
        self._values: TTLCache[Hashable, V] = TTLCache(maxsize=maxsize, ttl=ttl_s, timer=clock)
        self._flights: dict[Hashable, _Flight[V]] = {}

    async def get(
        self, key: Hashable, fetch: Callable[[], Awaitable[V]], *, keep: Callable[[V], bool] = _keep_all
    ) -> tuple[V, bool]:
        """The value for `key`, and whether it was cached or shared instead of fetched by this call.

        A fetch that fails fails for every caller that was waiting for it, so one broken request is not
        retried once per caller. `keep` decides whether a fetched value is stored for later callers;
        the callers already waiting for this fetch get the value either way, as it answers them too.
        """
        while True:
            if (cached := self._values.get(key)) is not None:
                return cached, True
            flight = self._flights.get(key)
            if flight is None:
                return await self._lead(key, fetch, keep), False
            await flight.done.wait()
            if flight.error is not None:
                raise flight.error
            if flight.value is not None:
                return flight.value, True
            # The fetch was cancelled, for instance by its own request's deadline: lead, or follow the next.

    async def _lead(self, key: Hashable, fetch: Callable[[], Awaitable[V]], keep: Callable[[V], bool]) -> V:
        flight = self._flights[key] = _Flight[V]()
        try:
            value = await fetch()
            if keep(value):
                self._values[key] = value
            flight.value = value
            return value
        except Exception as error:
            flight.error = error
            raise
        finally:
            del self._flights[key]
            flight.done.set()
