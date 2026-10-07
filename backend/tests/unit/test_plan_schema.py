"""The model-facing plan is accepted by OpenAI strict mode: linted through the SDK's own converter."""

import json
from collections.abc import Iterator
from typing import Any

import pytest
from openai.lib._pydantic import to_strict_json_schema
from syrupy.assertion import SnapshotAssertion

from ctviz.contract.plan import QueryPlan

# Measured live: these keywords were accepted by strict mode and the rest of the JSON Schema was not.
ACCEPTED_KEYWORDS = {
    "$defs", "$ref", "additionalProperties", "anyOf", "const", "description", "enum", "items",
    "maxItems", "maximum", "minimum", "properties", "required", "title", "type",
}  # fmt: skip
MAX_PROPERTIES, MAX_ENUM_VALUES, MAX_DEPTH = 5000, 1000, 10


@pytest.fixture(scope="module")
def wire_schema() -> dict[str, Any]:
    return to_strict_json_schema(QueryPlan)


def _schemas(node: Any, depth: int = 0) -> Iterator[tuple[dict[str, Any], int]]:
    """Every schema node with its property nesting depth; `$defs` entries restart at depth 0."""
    if isinstance(node, dict):
        yield node, depth
        for key, child in node.items():
            if key in ("properties", "$defs"):
                for sub in child.values():
                    yield from _schemas(sub, depth + 1 if key == "properties" else 0)
            elif key not in ("enum", "required"):
                yield from _schemas(child, depth)
    elif isinstance(node, list):
        for child in node:
            yield from _schemas(child, depth)


def test_only_accepted_keywords_are_used(wire_schema: dict[str, Any]) -> None:
    keywords = {key for node, _ in _schemas(wire_schema) for key in node}

    assert keywords <= ACCEPTED_KEYWORDS


def test_every_object_is_closed_and_requires_all_of_its_properties(wire_schema: dict[str, Any]) -> None:
    objects = [node for node, _ in _schemas(wire_schema) if "properties" in node]

    assert objects
    for node in objects:
        assert node["additionalProperties"] is False
        assert set(node["required"]) == set(node["properties"])


def test_the_schema_is_within_the_documented_limits(wire_schema: dict[str, Any]) -> None:
    nodes = list(_schemas(wire_schema))
    properties = sum(len(node["properties"]) for node, _ in nodes if "properties" in node)
    enum_values = sum(len(node["enum"]) for node, _ in nodes if "enum" in node)
    deepest = max(depth for _, depth in nodes)

    assert properties <= MAX_PROPERTIES
    assert enum_values <= MAX_ENUM_VALUES
    assert deepest <= MAX_DEPTH


def test_no_union_is_a_one_of_or_a_discriminator(wire_schema: dict[str, Any]) -> None:
    text = json.dumps(wire_schema)

    assert "oneOf" not in text
    assert "discriminator" not in text
    assert "uniqueItems" not in text


def test_the_wire_schema_snapshot(wire_schema: dict[str, Any], snapshot: SnapshotAssertion) -> None:
    assert wire_schema == snapshot
