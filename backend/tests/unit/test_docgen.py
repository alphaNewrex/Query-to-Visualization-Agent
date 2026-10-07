"""The schema export: what a type generator reads from `docs/schema/`, and that the files are fresh."""

import json
from pathlib import Path
from typing import Any

import pytest

from ctviz.docgen import (
    OPENAPI_FILE,
    contract_documents,
    schema_documents,
    stale_schema_files,
    write_schema_files,
)

# The type names the frontend imports from the generated file.
FRONTEND_IMPORTS = [
    "QueryRequest",
    "AnalysisRequest",
    "QueryPlan",
    "QueryResponse",
    "ErrorResponse",
    "Datum",
    "Citation",
]


@pytest.fixture(scope="module")
def combined() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(contract_documents()["contract.v1.schema.json"])
    return document


def test_the_combined_schema_holds_every_top_level_type_once(combined: dict[str, Any]) -> None:
    assert list(combined["properties"]) == [
        "query_request",
        "analysis_request",
        "query_plan",
        "query_response",
        "error_response",
    ]
    for name in FRONTEND_IMPORTS:
        assert name in combined["$defs"]


def test_the_two_unions_are_named_definitions_so_that_a_generator_can_export_them(
    combined: dict[str, Any],
) -> None:
    kinds = combined["$defs"]["QueryResponse"]
    types = combined["$defs"]["Visualization"]

    assert kinds["discriminator"]["propertyName"] == "kind"
    assert types["discriminator"]["propertyName"] == "type"
    assert len(kinds["oneOf"]) == 3
    assert len(types["oneOf"]) == 7


def test_a_response_model_lists_every_key_as_required_and_a_request_model_keeps_optional_keys(
    combined: dict[str, Any],
) -> None:
    definitions = combined["$defs"]

    for name in ("VisualizationResponse", "Meta", "Datum"):
        assert set(definitions[name]["required"]) == set(definitions[name]["properties"])
    assert definitions["QueryRequest"]["required"] == ["query"]
    assert definitions["RequestOptions"].get("required", []) == []


def test_a_row_may_carry_scalar_keys_beyond_its_reserved_ones(combined: dict[str, Any]) -> None:
    scalars = combined["$defs"]["Datum"]["additionalProperties"]["anyOf"]

    assert {branch["type"] for branch in scalars} == {"string", "integer", "number", "boolean", "null"}
    assert combined["$defs"]["Citation"]["additionalProperties"] is False


def test_properties_carry_no_title_of_their_own(combined: dict[str, Any]) -> None:
    properties = combined["$defs"]["QueryRequest"]["properties"]

    assert all("title" not in schema for schema in properties.values())


def test_the_request_documents_its_bounds(combined: dict[str, Any]) -> None:
    properties = combined["$defs"]["QueryRequest"]["properties"]

    assert (properties["query"]["minLength"], properties["query"]["maxLength"]) == (1, 1000)
    assert properties["top_n"]["anyOf"][0]["maximum"] == 50


def test_the_output_is_deterministic_and_keeps_declaration_order(combined: dict[str, Any]) -> None:
    assert contract_documents() == contract_documents()
    assert list(combined["$defs"]["QueryRequest"]["properties"])[:3] == ["query", "drug_name", "condition"]
    assert list(combined["$defs"]["BarChart"]["properties"])[:4] == [
        "type",
        "title",
        "subtitle",
        "orientation",
    ]


def test_the_committed_schema_files_are_up_to_date(real_repository_root: Path) -> None:
    directory = real_repository_root / "docs" / "schema"

    stale = [
        name
        for name, text in contract_documents().items()
        if not (directory / name).is_file() or (directory / name).read_text(encoding="utf-8") != text
    ]

    assert stale == [], "run `uv run python scripts/gen_docs.py` from backend/"


def test_written_files_are_fresh_and_a_changed_file_is_stale(tmp_path: Path) -> None:
    written = write_schema_files(tmp_path)

    assert {path.name for path in written} == set(schema_documents())
    assert stale_schema_files(tmp_path) == []

    (tmp_path / OPENAPI_FILE).write_text("{}\n", encoding="utf-8")
    (tmp_path / "error-response.v1.schema.json").unlink()

    assert sorted(stale_schema_files(tmp_path)) == ["error-response.v1.schema.json", OPENAPI_FILE]
