"""Structured mode: the plan built from the request's fields alone, with no model."""

from ctviz.contract.plan import Aggregate, Entity, PlanFilters, QueryPlan
from ctviz.contract.request import QueryRequest
from ctviz.planning.grounding import ENTITY_FIELDS, tokens

NO_FILTERS = PlanFilters(
    phases=[],
    statuses=[],
    study_types=[],
    sponsor_classes=[],
    intervention_types=[],
    evidence=[],
    date_field=None,
    year_from=None,
    year_to=None,
)


def plan_from_fields(request: QueryRequest) -> QueryPlan:
    """Entity fields become filters, `compare` becomes compared entities and `group_by` the aggregate.

    `group_by` is required in structured mode, so its absence is a programming error here, not a
    request error.
    """
    if request.group_by is None:
        raise ValueError("Structured mode needs group_by.")
    dimension, *rest = request.group_by
    series = rest[0] if rest else None
    compared: list[Entity] = []
    if request.compare is not None:
        kind = ENTITY_FIELDS[request.compare.field]
        compared = [Entity(kind=kind, value=value, role="compare") for value in request.compare.values]
    # A field value that is one of the compared values is not an extra filter: the comparison is kept.
    taken = {(entity.kind, tokens(entity.value)) for entity in compared}
    entities = [
        Entity(kind=kind, value=value, role="filter")
        for name, kind in ENTITY_FIELDS.items()
        for value in getattr(request, name) or []
        if (kind, tokens(value)) not in taken
    ]
    entities += compared
    filters = NO_FILTERS.model_copy(
        update={
            "phases": request.trial_phase or [],
            "statuses": request.status or [],
            "study_types": request.study_type or [],
            "sponsor_classes": request.sponsor_class or [],
            "intervention_types": request.intervention_type or [],
            "date_field": request.date_field,
            "year_from": request.start_year,
            "year_to": request.end_year,
        }
    )
    grouping = dimension if series is None else f"{dimension} and {series}"
    return QueryPlan(
        interpretation=f"Count trials by {grouping}, as set by the request's fields.",
        entities=entities,
        filters=filters,
        analysis=Aggregate(
            kind="aggregate",
            dimension=dimension,
            series=series,
            time_unit=request.time_unit,
            top_n=request.top_n,
        ),
        chart_preference=request.chart_type,
    )
