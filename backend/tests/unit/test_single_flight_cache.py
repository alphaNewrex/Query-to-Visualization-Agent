"""`SingleFlightCache.get(keep=...)`: a value that is not kept still answers the callers waiting for it."""

import anyio
import pytest

from ctviz.ctgov.cache import SingleFlightCache

pytestmark = pytest.mark.anyio


class Fetches:
    """A fetch that counts its runs and answers `run number` after a pause, so that callers overlap."""

    def __init__(self, pause_s: float = 0.0) -> None:
        self.runs = 0
        self._pause_s = pause_s

    async def __call__(self) -> int:
        self.runs += 1
        await anyio.sleep(self._pause_s)
        return self.runs


def cache() -> SingleFlightCache[int]:
    return SingleFlightCache[int](maxsize=4, ttl_s=60.0, clock=lambda: 0.0)


async def test_a_kept_value_is_served_to_later_callers_without_a_second_fetch() -> None:
    fetches, values = Fetches(), cache()

    first = await values.get("k", fetches, keep=lambda value: True)
    second = await values.get("k", fetches, keep=lambda value: True)

    assert (first, second) == ((1, False), (1, True))
    assert fetches.runs == 1


async def test_a_value_that_is_not_kept_is_fetched_again_by_the_next_caller() -> None:
    fetches, values = Fetches(), cache()

    first = await values.get("k", fetches, keep=lambda value: False)
    second = await values.get("k", fetches, keep=lambda value: False)

    assert (first, second) == ((1, False), (2, False))


async def test_callers_waiting_for_a_value_that_is_not_kept_get_it_and_nothing_is_stored() -> None:
    fetches, values = Fetches(pause_s=0.05), cache()
    answers: list[tuple[int, bool]] = []

    async def ask() -> None:
        answers.append(await values.get("k", fetches, keep=lambda value: False))

    async with anyio.create_task_group() as group:
        for _ in range(3):
            group.start_soon(ask)

    assert fetches.runs == 1
    assert sorted(answers) == [(1, False), (1, True), (1, True)]
    assert await values.get("k", fetches, keep=lambda value: False) == (2, False)


async def test_every_value_is_kept_unless_the_caller_says_otherwise() -> None:
    fetches, values = Fetches(), cache()

    await values.get("k", fetches)

    assert await values.get("k", fetches) == (1, True)
