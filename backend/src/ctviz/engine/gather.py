"""Run independent awaitables at once and keep their results in the order they were given."""

from collections.abc import Awaitable, Callable, Sequence

import anyio


async def gather[T](calls: Sequence[Callable[[], Awaitable[T]]]) -> list[T]:
    """Start every call in order, wait for all of them, and return the results in the same order.

    The client's own concurrency cap keeps the number of requests in flight low, so starting all of
    them at once only fixes the order in which they are logged. The first failure cancels the rest.
    """
    results: dict[int, T] = {}

    async def run(position: int, call: Callable[[], Awaitable[T]]) -> None:
        results[position] = await call()

    async with anyio.create_task_group() as group:
        for position, call in enumerate(calls):
            group.start_soon(run, position, call)
    return [results[position] for position in range(len(calls))]
