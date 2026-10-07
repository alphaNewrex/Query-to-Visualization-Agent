"""HTTP routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

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
