"""Structured request fields win over what the model read (rules 3 to 5 of the plan checks)."""

from typing import Final

from ctviz.contract.plan import Aggregate, Entity, FilterFamily, PlanModel, QueryPlan, Total, TrialList
from ctviz.contract.request import QueryRequest
from ctviz.planning.findings import Findings
from ctviz.planning.grounding import ENTITY_FIELDS, tokens

_FAMILY_FIELDS: Final[dict[FilterFamily, str]] = {
    "phases": "trial_phase",
    "statuses": "status",
    "study_types": "study_type",
    "sponsor_classes": "sponsor_class",
    "intervention_types": "intervention_type",
}


def merge_request(plan: QueryPlan, request: QueryRequest, found: Findings) -> QueryPlan:
    """Replace the plan's entities, filters and hints by those the request states, recording each conflict."""
    plan = plan.model_copy(update={"entities": _merge_entities(list(plan.entities), request, found)})
    plan = _merge_filters(plan, request, found)
    plan = _merge_hints(plan, request, found)
    if request.chart_type is not None:
        if plan.chart_preference not in (None, request.chart_type):
            found.adjust(
                "hint_override",
                "/chart_preference",
                "The chart_type field replaced the planner's preference.",
                "replaced",
            )
        plan = plan.model_copy(update={"chart_preference": request.chart_type})
    return plan


def _key(entity: Entity) -> tuple[str, tuple[str, ...]]:
    return entity.kind, tokens(entity.value)


def _merge_entities(entities: list[Entity], request: QueryRequest, found: Findings) -> list[Entity]:
    if request.compare is not None:
        kind = ENTITY_FIELDS[request.compare.field]
        compared = [Entity(kind=kind, value=value, role="compare") for value in request.compare.values]
        read = [entity for entity in entities if entity.role == "compare"]
        if read and {_key(entity) for entity in read} != {_key(entity) for entity in compared}:
            found.adjust(
                "field_override",
                "/entities",
                "The compare field replaced the comparison the planner read.",
                "replaced",
            )
        entities = [entity for entity in entities if entity.role == "filter"] + compared
    compared_keys = {_key(entity) for entity in entities if entity.role == "compare"}
    for name, kind in ENTITY_FIELDS.items():
        values = [
            value for value in getattr(request, name) or [] if (kind, tokens(value)) not in compared_keys
        ]
        if not values:
            continue
        wanted = {tokens(value) for value in values}
        if any(e.kind == kind and e.role == "filter" and tokens(e.value) not in wanted for e in entities):
            found.adjust(
                "field_override",
                "/entities",
                f"The {name} field replaced the {kind} the planner read.",
                "replaced",
            )
        entities = [e for e in entities if not (e.kind == kind and e.role == "filter")]
        entities += [Entity(kind=kind, value=value, role="filter") for value in values]
    return entities


def _merge_filters(plan: QueryPlan, request: QueryRequest, found: Findings) -> QueryPlan:
    changes: dict[str, object] = {}
    evidence = list(plan.filters.evidence)
    for family, name in _FAMILY_FIELDS.items():
        wanted = getattr(request, name)
        if not wanted:
            continue
        current = getattr(plan.filters, family)
        if current and set(current) != set(wanted):
            found.adjust(
                "field_override",
                f"/filters/{family}",
                f"The {name} field replaced the planner's values.",
                "replaced",
            )
        changes[family] = list(wanted)
        evidence = [item for item in evidence if item.family != family]
    changes["evidence"] = evidence
    for name, attribute in (
        ("start_year", "year_from"),
        ("end_year", "year_to"),
        ("date_field", "date_field"),
    ):
        wanted_value = getattr(request, name)
        if wanted_value is None:
            continue
        current_value = getattr(plan.filters, attribute)
        if current_value not in (None, wanted_value):
            found.adjust(
                "field_override",
                f"/filters/{attribute}",
                f"The {name} field replaced the planner's value.",
                "replaced",
            )
        changes[attribute] = wanted_value
    return plan.model_copy(update={"filters": plan.filters.model_copy(update=changes)})


def _merge_hints(plan: QueryPlan, request: QueryRequest, found: Findings) -> QueryPlan:
    analysis = plan.analysis
    if request.group_by is not None and isinstance(analysis, Aggregate | Total):
        dimension, series = request.group_by[0], (request.group_by[1] if len(request.group_by) == 2 else None)
        read = (analysis.dimension, analysis.series) if isinstance(analysis, Aggregate) else None
        if read != (dimension, series):
            found.adjust(
                "hint_override",
                "/analysis",
                "The group_by field replaced the planner's grouping.",
                "replaced",
            )
        analysis = Aggregate(
            kind="aggregate",
            dimension=dimension,
            series=series,
            time_unit=analysis.time_unit if isinstance(analysis, Aggregate) else None,
            top_n=analysis.top_n if isinstance(analysis, Aggregate) else None,
        )
    if isinstance(analysis, Aggregate):
        analysis = _override(analysis, "time_unit", request.time_unit, found)
        analysis = _override(analysis, "top_n", request.top_n, found)
    elif request.top_n is not None and isinstance(analysis, TrialList):
        analysis = _override(analysis, "limit", request.top_n, found)
    return plan.model_copy(update={"analysis": analysis})


def _override[A: PlanModel](analysis: A, attribute: str, wanted: object, found: Findings) -> A:
    if wanted is None:
        return analysis
    if getattr(analysis, attribute) not in (None, wanted):
        found.adjust(
            "hint_override",
            f"/analysis/{attribute}",
            f"The request's {attribute} replaced the planner's.",
            "replaced",
        )
    return analysis.model_copy(update={attribute: wanted})
