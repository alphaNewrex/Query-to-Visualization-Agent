"""Hand-written contract documents shared by the contract tests: the plan's printed examples, completed."""

from copy import deepcopy
from typing import Any

from pydantic import TypeAdapter

from ctviz.contract.response import QueryResponse

RESPONSES: TypeAdapter[Any] = TypeAdapter(QueryResponse)

PATH = "protocolSection.statusModule.startDateStruct.date"

FILTERS: dict[str, Any] = {
    "drug_name": ["pembrolizumab"], "condition": [], "sponsor": [], "country": [], "term": [],
    "trial_phase": [], "status": [], "study_type": [], "sponsor_class": [], "intervention_type": [],
    "sex": [], "age_group": [], "allocation": [], "masking": [], "primary_purpose": [], "has_results": [],
    "intervention_model": [],
    "start_year": 2015, "end_year": None, "date_field": "start_date", "compare": None,
    "exclude": {"drug_name": [], "condition": [], "sponsor": [], "country": [], "term": [], "status": []},
}  # fmt: skip

PLAN: dict[str, Any] = {
    "interpretation": "Count pembrolizumab trials by start year since 2015.",
    "entities": [{"kind": "drug", "value": "pembrolizumab", "role": "filter"}],
    "filters": {
        "phases": [], "statuses": [], "study_types": [], "sponsor_classes": [], "intervention_types": [],
        "exclude_statuses": [], "sexes": [], "age_groups": [], "allocations": [], "maskings": [],
        "primary_purposes": [], "has_results": [], "intervention_models": [],
        "evidence": [], "date_field": None, "year_from": 2015, "year_to": None,
    },
    "analysis": {"kind": "aggregate", "dimension": "start_date", "series": None, "time_unit": "year",
                 "top_n": None, "statistic": None, "of": None},
    "chart_preference": None,
    "unapplied": [],
}  # fmt: skip


def meta(*, trials_cited: int, max_per_datum: int = 5, plan: dict[str, Any] | None = PLAN) -> dict[str, Any]:
    return {
        "request_id": "0f3c7c1e-0000-0000-0000-000000000000",
        "generated_at": "2026-10-06T12:00:00Z",
        "query": "How has the number of trials for pembrolizumab changed per year since 2015?",
        "filters": FILTERS,
        "interpretation": None,
        "plan": plan,
        "options": {},
        "planner": {
            "mode": "llm", "model": None, "reasoning_effort": None, "prompt_version": "plan-v1",
            "attempts": 1, "is_repaired": False, "is_fallback": False, "usage": None,
        },
        "assumptions": [],
        "warnings": [{"code": "partial_period", "message": "2026 is incomplete: data as of 2026-10-06."}],
        "source": None,
        "counts": {
            "data_points": 3,
            "series": [{"label": None, "trials_matched": 12, "trials_analyzed": 10,
                        "trials_excluded": [{"reason": "no_start_date", "count": 2, "message": "None."}]}],
            "trials_in_several_series": None,
        },
        "truncation": {"is_truncated": False, "items": []},
        "citations": {"is_enabled": True, "max_per_datum": max_per_datum, "selection": "most relevant",
                      "trials_cited": trials_cited},
        "suggested_followups": [],
        "cache": {"is_plan_cached": False, "is_response_cached": False, "cached_at": None},
        "timing": {"total_ms": 1, "plan_ms": 1, "resolve_ms": 1, "fetch_ms": 1, "build_ms": 1},
        "debug": None,
    }  # fmt: skip


def row(period: str, count: int, *nct_ids: str) -> dict[str, Any]:
    return {
        "start_year": period, "trial_count": count, "citation_count": count, "source_url": None,
        "citations": [{"nct_id": n, "field": PATH, "excerpt": f"{period}-01"} for n in nct_ids],
    }  # fmt: skip


def time_series_response() -> dict[str, Any]:
    """Three years, 4 + 3 + 3 = 10 trials analyzed, two cited trials."""
    rows = [row("2015", 4, "NCT00000001"), row("2016", 3, "NCT00000002"), row("2017", 3)]
    reference = {
        "title": "A trial",
        "url": "https://clinicaltrials.gov/study/NCT00000001",
        "scope_evidence": [],
    }
    return {
        "spec_version": "1.0",
        "kind": "visualization",
        "message": "4 trials started in 2015.",
        "visualization": {
            "type": "time_series", "title": "Trials started per year", "subtitle": None,
            "mark": "line", "stack": "none",
            "encoding": {
                "x": {"field": "start_year", "type": "temporal", "title": "Start year", "time_unit": "year"},
                "y": {"field": "trial_count", "type": "quantitative", "title": "Trials started",
                      "unit": "trials", "format": ",d", "scale": "linear"},
                "series": None, "tooltip": [],
            },
            "data": rows,
        },
        "clarification": None,
        "references": {
            "NCT00000001": reference,
            "NCT00000002": {**reference, "url": "https://clinicaltrials.gov/study/NCT00000002"},
        },
        "meta": meta(trials_cited=2),
    }  # fmt: skip


def clarification_response() -> dict[str, Any]:
    document = deepcopy(time_series_response())
    document.update(
        kind="clarification",
        message="Which drug do you mean? Name it in the question or send `drug_name`.",
        visualization=None,
        clarification={"reason": "missing_entity", "missing_fields": ["drug_name"], "options": []},
        references={},
    )
    document["meta"] = {**meta(trials_cited=0), "interpretation": None, "source": None, "counts": None}
    return document
