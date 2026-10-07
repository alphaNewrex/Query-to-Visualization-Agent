"""A token bucket: the registry has no documented rate limit, so a burst is allowed and the rest spaced."""

from collections.abc import Awaitable, Callable

import anyio


class TokenBucket:
    """Lets `burst` requests through at once, and then `rate_per_s` more every second."""

    def __init__(
        self,
        burst: int,
        rate_per_s: float,
        clock: Callable[[], float],
        sleep: Callable[[float], Awaitable[None]],
    ) -> None:
        if burst < 1 or rate_per_s <= 0:
            raise ValueError("A token bucket needs a burst of at least 1 and a positive rate.")
        self._capacity = float(burst)
        self._rate = rate_per_s
        self._tokens = float(burst)
        self._clock = clock
        self._updated = clock()
        self._sleep = sleep
        self._turn = anyio.Lock()  # waiters queue here, so tokens go out in the order they were asked for

    async def take(self) -> None:
        """Wait until a token is free, and take it."""
        async with self._turn:
            while True:
                now = self._clock()
                self._tokens = min(self._capacity, self._tokens + (now - self._updated) * self._rate)
                self._updated = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                await self._sleep((1 - self._tokens) / self._rate)
