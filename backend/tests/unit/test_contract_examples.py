"""The plan's printed examples validate against the contract models and survive a round trip."""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from ctviz.contract.plan import QueryPlan
from ctviz.contract.request import AnalysisRequest, QueryRequest
from ctviz.contract.response import ClarificationResponse, ErrorResponse, VisualizationResponse
from tests.unit.contract_samples import PLAN, RESPONSES, clarification_response, time_series_response

REQUESTS: list[dict[str, Any]] = [
    {"query": "How has the number of trials for this drug changed over time?", "drug_name": "Pembrolizumab"},
    {"query": "Compare phases", "compare": {"field": "drug_name", "values": ["pembrolizumab", "nivolumab"]},
     "group_by": ["phase"], "options": {"planner": "structured"}},
    {"query": "Industry-sponsored breast cancer trials in the United States per year", "country": "US",
     "sponsor_class": ["INDUSTRY"], "trial_phase": ["PHASE2", "PHASE3"], "start_year": 2020},
]  # fmt: skip


@pytest.mark.parametrize("body", REQUESTS)
def test_the_requests_of_the_plan_validate(body: dict[str, Any]) -> None:
    request = QueryRequest.model_validate(body)

    assert request.query == body["query"]


def test_a_time_series_response_validates_and_dumps_every_key() -> None:
    document = time_series_response()

    response = RESPONSES.validate_python(document)
    dumped = RESPONSES.dump_python(response, mode="json")

    assert isinstance(response, VisualizationResponse)
    assert dumped["clarification"] is None
    assert dumped["visualization"]["encoding"]["series"] is None
    assert dumped["visualization"]["data"][2]["citations"] == []
    assert dumped["visualization"]["data"][0]["start_year"] == "2015"


def test_the_clarification_example_validates() -> None:
    response = RESPONSES.validate_python(clarification_response())

    assert isinstance(response, ClarificationResponse)
    assert response.visualization is None
    assert response.clarification.missing_fields == ["drug_name"]


def test_an_unknown_visualization_type_is_rejected() -> None:
    document = time_series_response()
    document["visualization"]["type"] = "pie_chart"

    with pytest.raises(ValidationError):
        RESPONSES.validate_python(document)


def test_a_row_accepts_scalar_keys_only() -> None:
    document = time_series_response()
    document["visualization"]["data"][0]["nested"] = {"not": "a scalar"}

    with pytest.raises(ValidationError):
        RESPONSES.validate_python(document)


def test_a_citation_needs_a_well_formed_nct_id() -> None:
    document = time_series_response()
    document["visualization"]["data"][0]["citations"][0]["nct_id"] = "NCT123"

    with pytest.raises(ValidationError):
        RESPONSES.validate_python(document)


def test_an_unknown_key_in_a_response_model_is_rejected() -> None:
    document = time_series_response()
    document["meta"]["extra"] = 1

    with pytest.raises(ValidationError):
        RESPONSES.validate_python(document)


def test_the_error_example_validates() -> None:
    body = {
        "error": {
            "code": "invalid_request",
            "message": "Unknown field 'drug'. Did you mean 'drug_name'?",
            "details": {
                "errors": [{"path": "/drug", "code": "extra_forbidden", "message": "Unknown field."}]
            },
            "request_id": "0f3c7c1e-0000",
            "is_retryable": False,
        }
    }

    assert ErrorResponse.model_validate(body).error.code == "invalid_request"


def test_the_plan_replays_through_an_analysis_request() -> None:
    plan = QueryPlan.model_validate(PLAN)

    replay = AnalysisRequest.model_validate({"plan": json.loads(plan.model_dump_json()), "options": {}})

    assert replay.plan == plan
