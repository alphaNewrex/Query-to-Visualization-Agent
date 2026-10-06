"""Fixtures for tests that call the application over `httpx2.ASGITransport`: no socket, no server."""

from collections.abc import AsyncIterator

import httpx2
import pytest
from fastapi import FastAPI

from ctviz.api.app import create_app
from ctviz.settings import Settings


@pytest.fixture
def request_deadline_s() -> float:
    """The request deadline of the application under test: the default, unless a test parametrizes it."""
    return Settings().request_deadline_s


@pytest.fixture
def settings(request_deadline_s: float) -> Settings:
    return Settings(request_deadline_s=request_deadline_s)


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    """The real application. A test may add routes of its own before the first request."""
    return create_app(settings)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx2.AsyncClient]:
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http
