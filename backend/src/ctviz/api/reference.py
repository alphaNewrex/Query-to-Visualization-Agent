"""Read-only reference routes: capabilities, the committed schemas and the recorded examples."""

from functools import cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, Response

from ctviz import docgen, examples
from ctviz.api.routes import get_deps
from ctviz.capabilities import Document, capabilities
from ctviz.pipeline import Deps

router = APIRouter()

_SCHEMA_SUFFIX = ".v1.schema.json"


@cache
def _schemas() -> dict[str, str]:
    """Generated once: the schemas depend on the code alone, and the image holds no `docs/schema`."""
    return docgen.contract_documents()


@router.get("/v1/capabilities", summary="Dimensions, limits and planner state")
async def get_capabilities(deps: Annotated[Deps, Depends(get_deps)]) -> Document:
    return capabilities(deps.catalog, deps.settings)


@router.get("/v1/schema/{name}", summary="A JSON Schema of the contract")
async def get_schema(name: str) -> Response:
    """`contract`, `query-request`, `query-plan`, `query-response` or `error-response`."""
    text = _schemas().get(f"{name}{_SCHEMA_SUFFIX}")
    if text is None:
        raise HTTPException(status_code=404, detail=f"There is no schema named '{name}'.")
    return Response(text, media_type="application/schema+json")


@router.get("/v1/examples", summary="The recorded example runs")
async def list_examples(deps: Annotated[Deps, Depends(get_deps)]) -> list[examples.Document]:
    return examples.summaries(examples.examples_directory(deps.settings))


@router.get("/v1/examples/{slug}", summary="One recorded run: request, plan and response")
async def get_example(slug: str, deps: Annotated[Deps, Depends(get_deps)]) -> JSONResponse:
    found = examples.load_example(examples.examples_directory(deps.settings), slug)
    if found is None:
        raise HTTPException(status_code=404, detail=f"There is no example named '{slug}'.")
    return JSONResponse(found)
