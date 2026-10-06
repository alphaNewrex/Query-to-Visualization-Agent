"""The service's own routes."""

import httpx2
import pytest

pytestmark = pytest.mark.anyio


async def test_healthz_says_the_process_is_up_even_without_a_key(client: httpx2.AsyncClient) -> None:
    response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
