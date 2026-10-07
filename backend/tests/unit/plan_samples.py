"""Plans and requests for the planning tests, built from small overrides of one valid plan."""

from datetime import date
from typing import Any

from ctviz.contract.plan import QueryPlan
from ctviz.contract.request import QueryRequest

TODAY = date(2026, 10, 6)

BASE: dict[str, Any] = {
    "interpretation": "Count pembrolizumab trials by phase.",
    "entities": [{"kind": "drug", "value": "pembrolizumab", "role": "filter"}],
    "filters": {
        "phases": [], "statuses": [], "study_types": [], "sponsor_classes": [], "intervention_types": [],
        "exclude_statuses": [], "sexes": [], "age_groups": [], "allocations": [], "maskings": [],
        "primary_purposes": [], "has_results": [], "intervention_models": [],
        "evidence": [], "date_field": None, "year_from": None, "year_to": None,
    },
    "analysis": {"kind": "aggregate", "dimension": "phase", "series": None, "time_unit": None, "top_n": None,
                 "statistic": None, "of": None},
    "chart_preference": None,
    "unapplied": [],
}  # fmt: skip


def entity(kind: str, value: str, role: str = "filter") -> dict[str, str]:
    return {"kind": kind, "value": value, "role": role}


def plan(
    *,
    entities: list[dict[str, str]] | None = None,
    analysis: dict[str, Any] | None = None,
    filters: dict[str, Any] | None = None,
    **top: Any,
) -> QueryPlan:
    """The base plan with the given parts replaced; `analysis` is replaced whole, `filters` key by key."""
    document = {**BASE, **top}
    if entities is not None:
        document["entities"] = entities
    if analysis is not None:
        document["analysis"] = analysis
    document["filters"] = {**BASE["filters"], **(filters or {})}
    return QueryPlan.model_validate(document)


def aggregate(dimension: str = "phase", **fields: Any) -> dict[str, Any]:
    return {**BASE["analysis"], "dimension": dimension, **fields}


def request(
    query: str = "How are pembrolizumab trials distributed across phases?", **fields: Any
) -> QueryRequest:
    return QueryRequest.model_validate({"query": query, **fields})


class Countries:
    """A country table of three names, enough for the lookup rules."""

    NAMES = ("France", "United States", "Turkey (Türkiye)")

    def resolve(self, text: str) -> str | None:
        return next((name for name in self.NAMES if name.casefold() == text.casefold()), None)

    def suggest(self, text: str) -> list[str]:
        return [name for name in self.NAMES if name[0].casefold() == text[:1].casefold()]
