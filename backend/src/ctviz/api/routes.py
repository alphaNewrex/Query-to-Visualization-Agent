"""HTTP routes."""

from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ctviz.api.readiness import Readiness, check_readiness
from ctviz.api.stream import ProgressStream
from ctviz.contract.request import AnalysisRequest, QueryRequest
from ctviz.contract.response import QueryResponse
from ctviz.ctgov.context import RequestContext
from ctviz.pipeline import Deps, answer, execute

router = APIRouter()


def get_deps(request: Request) -> Deps:
    deps: Deps = request.app.state.deps
    return deps


def get_context(request: Request) -> RequestContext:
    return RequestContext(request_id=str(request.state.request_id))


@router.get("/healthz", summary="Liveness: the process is up")
async def healthz() -> dict[str, str]:
    """Answer without touching ClinicalTrials.gov or the planner, so it stays true when they are down."""
    return {"status": "ok"}


@router.get(
    "/readyz",
    summary="Readiness: the registry answers, and the configured models are listed by the key",
    response_model=Readiness,
    responses={503: {"model": Readiness, "description": "ClinicalTrials.gov does not answer."}},
)
async def readyz(deps: Annotated[Deps, Depends(get_deps)]) -> JSONResponse:
    """Ask the registry for its version and the model provider for its list, each within a few seconds.

    503 only when the registry is unreachable. The model list is reported and never decides the status.
    """
    report = await check_readiness(deps)
    return JSONResponse(report.model_dump(mode="json"), status_code=200 if report.status == "ready" else 503)


@router.post(
    "/v1/query",
    summary="Answer a question with a visualization specification",
    response_model=QueryResponse,
)
async def query(
    body: QueryRequest,
    deps: Annotated[Deps, Depends(get_deps)],
    ctx: Annotated[RequestContext, Depends(get_context)],
) -> JSONResponse:
    """Plan the question, run it against ClinicalTrials.gov, answer with a chart, a question or a message."""
    return _json(await answer(body, deps, ctx))


_STREAM_DOCUMENTATION = """\
`text/event-stream`. Zero or more `event: stage` events, each with data \
`{"step": "plan"|"check"|"resolve"|"strategy"|"execute"|"build", "status": "started"|"done", \
"summary": string, "detail": object}`, then exactly one `event: result` (the body of `/v1/query`) or one \
`event: error` (the error envelope). Errors found after the stream began are events, not statuses."""


@router.post(
    "/v1/query/stream",
    summary="Answer a question, reporting progress as server-sent events",
    description=_STREAM_DOCUMENTATION,
    response_class=ProgressStream,
    status_code=200,
    responses={
        200: {
            "description": "A stream of `stage` events and one `result` or `error` event.",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        }
    },
)
async def query_stream(
    body: QueryRequest,
    deps: Annotated[Deps, Depends(get_deps)],
    ctx: Annotated[RequestContext, Depends(get_context)],
) -> ProgressStream:
    """The same request and answer as `/v1/query`, with the stages as they happen.

    The request deadline is captured here, because the middleware stops enforcing it once the stream has
    begun; the stream enforces it itself and ends with an `error` event.
    """
    return ProgressStream(
        lambda progress: answer(body, deps, ctx, progress),
        request_id=ctx.request_id,
        deadline=anyio.current_effective_deadline(),
    )


@router.post(
    "/v1/analyses",
    summary="Run a typed plan, with no model call",
    response_model=QueryResponse,
)
async def analyses(
    body: AnalysisRequest,
    deps: Annotated[Deps, Depends(get_deps)],
    ctx: Annotated[RequestContext, Depends(get_context)],
) -> JSONResponse:
    """The same response as `/v1/query` for a plan that is already written: the replay path."""
    planned = deps.plans.accept(body, deps.clock().date())
    return _json(await execute(planned, deps, ctx))


def _json(response: QueryResponse) -> JSONResponse:
    # Never `exclude_none`: a null excerpt or an open bin end means something.
    return JSONResponse(response.model_dump(mode="json"))
