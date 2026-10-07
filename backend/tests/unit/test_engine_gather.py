"""gather(): results in call order, and a failure that keeps its own type."""

import anyio
import pytest

from ctviz.engine.gather import gather
from ctviz.errors import UpstreamUnavailable

pytestmark = pytest.mark.anyio


async def test_results_come_back_in_the_order_of_the_calls() -> None:
    async def slow() -> str:
        await anyio.sleep(0.01)
        return "first"

    async def fast() -> str:
        return "second"

    assert await gather([slow, fast]) == ["first", "second"]


async def test_no_calls_give_no_results() -> None:
    assert await gather([]) == []


async def test_a_failed_call_raises_its_own_error_not_a_group() -> None:
    """Without this the error handlers see an ExceptionGroup and answer 500 for a registry outage."""
    failure = UpstreamUnavailable("The registry did not answer.")

    async def fine() -> int:
        return 1

    async def broken() -> int:
        raise failure

    with pytest.raises(UpstreamUnavailable) as caught:
        await gather([fine, broken])
    assert caught.value is failure


async def test_a_failure_inside_a_nested_gather_is_unwrapped_too() -> None:
    async def broken() -> int:
        raise ValueError("inner")

    async def nested() -> list[int]:
        return await gather([broken])

    with pytest.raises(ValueError, match="inner"):
        await gather([nested])
