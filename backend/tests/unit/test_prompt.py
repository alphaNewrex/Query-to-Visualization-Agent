"""The instructions: the rules as printed, the glossary from the catalogue, the worked examples."""

import json
from datetime import date
from typing import get_args

import pytest

from ctviz.catalog.fields import CATALOG
from ctviz.contract.plan import DimensionKey
from ctviz.planning.prompt import (
    EXAMPLES,
    PROMPT_VERSION,
    RULES,
    Example,
    build_instructions,
    render_glossary,
    user_message,
)
from ctviz.planning.validate import check_plan
from tests.unit.plan_samples import request

# Entities of the assignment's questions and of the planner evaluation; the examples must not teach them.
EVALUATION_ENTITIES = {
    "pembrolizumab", "nivolumab", "keytruda", "opdivo", "duchenne", "muscular dystrophy", "lung cancer",
    "spinal muscular atrophy", "cystic fibrosis", "alzheimer", "breast cancer",
}  # fmt: skip


def test_the_version_names_the_current_prompt() -> None:
    assert PROMPT_VERSION == "plan-v4"


def test_the_rules_are_printed_in_the_instructions() -> None:
    instructions = build_instructions()
    assert instructions.startswith("You translate a question about clinical trials")
    assert RULES in instructions


def test_the_glossary_names_every_dimension_of_the_vocabulary() -> None:
    glossary = render_glossary({})
    for key in get_args(DimensionKey):
        assert f"  {key}:" in glossary


def test_the_glossary_is_rendered_from_the_catalogue_when_it_has_entries() -> None:
    if not CATALOG:
        pytest.skip("the field catalogue is not filled yet")
    glossary = render_glossary(CATALOG)
    for spec in CATALOG.values():
        assert f"  {spec.key}: {spec.title} ({spec.kind})" in glossary


def test_there_are_fifteen_examples_that_share_no_entity_with_the_evaluation_questions() -> None:
    assert len(EXAMPLES) == 15
    shown = " ".join(
        entity.value.casefold() for example in EXAMPLES for entity in example.plan.entities
    ) + " ".join(example.request.query.casefold() for example in EXAMPLES)
    assert not [name for name in EVALUATION_ENTITIES if name in shown]


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda example: example.request.query[:40])
def test_every_example_plan_passes_the_checks_for_its_own_question(example: Example) -> None:
    result = check_plan(example.plan, example.request, mode="model", countries=None, today=date(2026, 1, 15))
    assert result.blocking == () and result.adjustments == ()


def test_the_user_message_carries_the_date_the_fields_and_the_question() -> None:
    message = user_message(request("This drug over time?", drug_name="Pembrolizumab"), date(2026, 10, 6))
    lines = message.splitlines()
    assert lines[0] == "Today: 2026-10-06"
    assert json.loads(lines[1].removeprefix("Structured fields: ")) == {"drug_name": ["Pembrolizumab"]}
    assert lines[2] == "Question: This drug over time?"


def test_the_user_message_says_when_there_are_no_fields() -> None:
    assert "Structured fields: none" in user_message(request("Phases?"), date(2026, 10, 6))
