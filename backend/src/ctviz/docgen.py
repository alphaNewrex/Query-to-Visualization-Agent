"""Documentation generated from the contract models: the JSON Schema files under `docs/schema/`.

One wrapper model, `Contract`, holds every top-level type, so a model that a request and a response
share is emitted once and a type generator can read the whole contract from one file. The schema is
exported in serialization mode, in which the response models list every key as required (the request
models that responses embed keep their optional keys).

The output is deterministic and not key-sorted: Pydantic sorts `$defs` and schema keywords by name and
keeps `properties` in declaration order, so a visualization reads `type`, `title`, `encoding`, `data`.
"""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, TypeAdapter
from pydantic.json_schema import GenerateJsonSchema

from ctviz.contract.plan import QueryPlan
from ctviz.contract.request import AnalysisRequest, QueryRequest
from ctviz.contract.response import ErrorResponse, QueryResponse
from ctviz.settings import REPOSITORY_ROOT, Settings

SCHEMA_DIRECTORY: Final = REPOSITORY_ROOT / "docs" / "schema"
OPENAPI_FILE: Final = "openapi.json"


class NoFieldTitles(GenerateJsonSchema):
    """Leave out the `title` Pydantic adds to every property.

    With them, a type generator turns each property into a separate named alias.
    """

    def field_title_should_be_set(self, schema: object) -> bool:
        return False


class Contract(BaseModel):
    """Every top-level type of the contract, one field each."""

    query_request: QueryRequest
    analysis_request: AnalysisRequest
    query_plan: QueryPlan
    query_response: QueryResponse
    error_response: ErrorResponse


# File name to the type it documents. The combined file comes first.
_SCHEMA_FILES: Final[Mapping[str, Any]] = {
    "contract.v1.schema.json": Contract,
    "query-request.v1.schema.json": QueryRequest,
    "query-plan.v1.schema.json": QueryPlan,
    "query-response.v1.schema.json": QueryResponse,
    "error-response.v1.schema.json": ErrorResponse,
}


def json_schema(documented: Any) -> dict[str, Any]:
    """The JSON Schema of a model or type alias, in serialization mode."""
    return TypeAdapter(documented).json_schema(mode="serialization", schema_generator=NoFieldTitles)


def openapi_document() -> dict[str, Any]:
    """The application's OpenAPI document, built from the default settings.

    Taken from `Settings.model_construct()` and not from `Settings()`, so that no environment
    variable and no env file (the real one holds a key) can change the output.
    """
    # Imported here: the contract schemas must not need the web framework.
    from ctviz.api.app import create_app

    return create_app(Settings.model_construct()).openapi()


def contract_documents() -> dict[str, str]:
    """The JSON Schema files, which depend on the contract models alone, as file name to text."""
    return {name: _text(json_schema(documented)) for name, documented in _SCHEMA_FILES.items()}


def schema_documents() -> dict[str, str]:
    """Every generated file as file name to text: the contract's schemas, then the OpenAPI document."""
    return {**contract_documents(), OPENAPI_FILE: _text(openapi_document())}


def write_schema_files(directory: Path = SCHEMA_DIRECTORY) -> list[Path]:
    """Write every generated file into `directory`, creating it if needed; return the paths."""
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, text in schema_documents().items():
        path = directory / name
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def stale_schema_files(directory: Path = SCHEMA_DIRECTORY) -> list[str]:
    """The names of generated files that are missing from `directory` or differ from a fresh export."""
    return [
        name
        for name, text in schema_documents().items()
        if not (directory / name).is_file() or (directory / name).read_text(encoding="utf-8") != text
    ]


def _text(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"
