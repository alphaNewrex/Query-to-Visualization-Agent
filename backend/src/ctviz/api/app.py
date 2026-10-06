"""The application factory, started with `uvicorn ctviz.api.app:create_app --factory`."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

from fastapi import FastAPI
from fastapi.middleware import Middleware
from fastapi.middleware.gzip import GZipMiddleware

from ctviz.api.handlers import install_exception_handlers
from ctviz.api.middleware import RequestContextMiddleware
from ctviz.api.routes import router
from ctviz.log import configure_logging
from ctviz.settings import Settings, load_settings, log_configuration_sources

_GZIP_MINIMUM_BYTES: Final = 1000


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the API.

    uvicorn calls a factory with no arguments, so none is required: without `settings`,
    they are loaded from the process environment and the root `.env`.
    """
    settings = settings if settings is not None else load_settings()
    configure_logging(settings.log_format)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        log_configuration_sources(settings)
        yield

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
    return app
