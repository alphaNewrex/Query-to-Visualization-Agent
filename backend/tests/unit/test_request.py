"""Validation and normalisation of `QueryRequest` (section 4.3 of the plan)."""

from typing import Any

import pytest
from pydantic import ValidationError

from ctviz.contract.request import QueryRequest


def make(**fields: Any) -> QueryRequest:
    return QueryRequest.model_validate({"query": "trials per year", **fields})


def test_whitespace_in_the_query_is_collapsed() -> None:
    assert make(query="  trials\n\tper   year ").query == "trials per year"


@pytest.mark.parametrize("query", ["ab", "123 456", "x" * 1001, "bad\x00query"])
def test_an_unusable_query_is_rejected(query: str) -> None:
    with pytest.raises(ValidationError):
        make(query=query)


@pytest.mark.parametrize(
    ("given", "expected"),
    [("Phase 2", ["PHASE2"]), ("phase2", ["PHASE2"]), ("2", ["PHASE2"]), ("II", ["PHASE2"]),
     ("Phase 2/3", ["PHASE2", "PHASE3"]), (["PHASE1", "Phase 3"], ["PHASE1", "PHASE3"])],
)  # fmt: skip
def test_lenient_phase_spellings_become_canonical_tokens(given: Any, expected: list[str]) -> None:
    assert make(trial_phase=given).trial_phase == expected


def test_an_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make(drug="pembrolizumab")


def test_an_empty_array_is_the_same_as_no_filter() -> None:
    assert make(country=[], status=[]).country is None
    assert make(country=[], status=[]).status is None


def test_year_bounds_must_be_ordered() -> None:
    with pytest.raises(ValidationError):
        make(start_year=2020, end_year=2010)


def test_a_comparison_needs_two_to_four_values() -> None:
    with pytest.raises(ValidationError):
        make(compare={"field": "drug_name", "values": ["only one"]})


def test_the_options_default_to_the_documented_values() -> None:
    options = make().options

    assert (options.planner, options.citations_per_datum, options.drug_match) == ("llm", 5, "broad")
    assert options.include_trace is True
    assert options.use_cache is True
