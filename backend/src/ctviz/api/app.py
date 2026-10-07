"""The application factory, started with `uvicorn ctviz.api.app:create_app --factory`."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Final

import httpx2
from fastapi import FastAPI
from fastapi.middleware import Middleware
from fastapi.middleware.gzip import GZipMiddleware

from ctviz.api.handlers import install_exception_handlers
from ctviz.api.middleware import RequestContextMiddleware
from ctviz.api.reference import router as reference_router
from ctviz.api.routes import router
from ctviz.catalog.countries import load_country_table
from ctviz.catalog.fields import CATALOG
from ctviz.ctgov.client import CtGovClient
from ctviz.engine.resolve import EntityResolver
from ctviz.log import configure_logging
from ctviz.pipeline import Deps, new_response_cache
from ctviz.planning.planner import Planner, build_planners
from ctviz.planning.service import PlanService
from ctviz.settings import Settings, load_settings, log_configuration_sources

_GZIP_MINIMUM_BYTES: Final = 1000


def create_app(
    settings: Settings | None = None,
    *,
    planner: Planner | None = None,
    transport: httpx2.AsyncBaseTransport | None = None,
) -> FastAPI:
    """Build the API.

    uvicorn calls a factory with no arguments, so none is required: without `settings`,
    they are loaded from the process environment and the root `.env`. A test passes its own
    `planner` (used with no fallback) and a `transport` standing in for ClinicalTrials.gov.
    """
    settings = settings if settings is not None else load_settings()
    configure_logging(settings.log_format)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        log_configuration_sources(settings)
        client = CtGovClient(settings, transport=transport)
        countries = load_country_table()
        first, fallback = (planner, None) if planner is not None else build_planners(settings)
        app.state.deps = Deps(
            settings=settings,
            ctgov=client,
            plans=PlanService(first, fallback, settings, countries=countries),
            resolver=EntityResolver(client, countries, low_match_threshold=settings.low_match_threshold),
            catalog=CATALOG,
            countries=countries,
            clock=lambda: datetime.now(UTC),
            responses=new_response_cache(settings.response_cache_size),
        )
        try:
            yield
        finally:
            await client.aclose()

    app = FastAPI(
        title="ClinicalTrials.gov Query-to-Visualization Agent",
        lifespan=lifespan,
        # Swagger UI at /docs and the OpenAPI document are the documented surface; ReDoc and
        # the OAuth2 redirect page that FastAPI would also serve are not.
        redoc_url=None,
        swagger_ui_oauth2_redirect_url=None,
        middleware=[
            # Outermost first: the request id and the deadline cover everything, compression included.
            Middleware(RequestContextMiddleware, deadline_s=settings.request_deadline_s),
            Middleware(GZipMiddleware, minimum_size=_GZIP_MINIMUM_BYTES),
        ],
    )
    install_exception_handlers(app)
    app.include_router(router)
    app.include_router(reference_router)
    return app
