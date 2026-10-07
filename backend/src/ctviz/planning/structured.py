"""Structured mode: the plan built from the request's fields alone, with no model."""

from ctviz.contract.plan import FAMILY_FIELDS, Aggregate, Entity, PlanFilters, QueryPlan
from ctviz.contract.request import QueryRequest
from ctviz.planning.grounding import ENTITY_FIELDS, tokens

NO_FILTERS = PlanFilters(
    phases=[],
    statuses=[],
    exclude_statuses=[],
    study_types=[],
    sponsor_classes=[],
    intervention_types=[],
    sexes=[],
    age_groups=[],
    allocations=[],
    maskings=[],
    primary_purposes=[],
    has_results=[],
    intervention_models=[],
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
    if request.exclude is not None:
        entities += [
            Entity(kind=kind, value=value, role="exclude")
            for name, kind in ENTITY_FIELDS.items()
            for value in getattr(request.exclude, name) or []
        ]
    filters = NO_FILTERS.model_copy(
        update={
            **{family: getattr(request, name) or [] for family, name in FAMILY_FIELDS.items()},
            "exclude_statuses": (request.exclude.status if request.exclude is not None else None) or [],
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
            statistic=None,
            of=None,
        ),
        chart_preference=request.chart_type,
        unapplied=[],
    )
