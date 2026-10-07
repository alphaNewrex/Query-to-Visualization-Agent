"""The closed vocabularies that several modules share stay in step with each other."""

from typing import get_args

from ctviz.contract.plan import (
    ClosedDimension,
    DimensionKey,
    EntityKind,
    FilterFamily,
    NodeKind,
    NumericField,
    PlanFilters,
    SortField,
)
from ctviz.contract.request import QueryRequest
from ctviz.contract.response import ErrorCodeName
from ctviz.errors import ErrorCode


def test_every_error_code_of_the_service_is_in_the_error_contract() -> None:
    # `backend_unreachable` is the one code that only the frontend's proxy emits.
    assert set(get_args(ErrorCodeName)) == {code.value for code in ErrorCode} | {"backend_unreachable"}


def test_a_series_is_always_a_dimension_a_question_can_group_by() -> None:
    assert set(get_args(ClosedDimension)) < set(get_args(DimensionKey))
    assert len(set(get_args(DimensionKey))) == len(get_args(DimensionKey)) == 21


def test_a_filter_family_names_a_list_of_the_plan_filters() -> None:
    assert set(get_args(FilterFamily)) <= set(PlanFilters.model_fields)


def test_a_sort_or_numeric_field_is_never_a_text_dimension() -> None:
    dimensions = set(get_args(DimensionKey))

    assert set(get_args(SortField)) - {"enrollment"} <= dimensions
    assert set(get_args(NumericField)) - {"duration_months", "site_count"} <= dimensions
    assert set(get_args(NodeKind)) <= dimensions


def test_the_entity_fields_of_the_request_are_the_entity_kinds_of_the_plan() -> None:
    entity_fields = {"drug_name", "condition", "sponsor", "country", "term"}

    assert entity_fields <= set(QueryRequest.model_fields)
    assert {kind if kind != "drug" else "drug_name" for kind in get_args(EntityKind)} == entity_fields
