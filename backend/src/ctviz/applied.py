"""The scope of a plan as request fields, so that `meta.filters` can be sent back as a request."""

from collections import defaultdict
from typing import Final, get_args

from ctviz.contract.plan import QueryPlan
from ctviz.contract.request import CompareSpec
from ctviz.contract.response import AppliedFilters

_FIELD_OF_KIND: Final = {
    "drug": "drug_name",
    "condition": "condition",
    "sponsor": "sponsor",
    "country": "country",
    "term": "term",
}
_COMPARABLE: Final = frozenset(get_args(CompareSpec.model_fields["field"].annotation))


def applied_filters(plan: QueryPlan) -> AppliedFilters:
    """Filter entities and enum filters as arrays, the comparison as `compare`."""
    filtered: defaultdict[str, list[str]] = defaultdict(list)
    compared: defaultdict[str, list[str]] = defaultdict(list)
    for entity in plan.entities:
        field = _FIELD_OF_KIND[entity.kind]
        (filtered if entity.role == "filter" else compared)[field].append(entity.value)
    compare = next(
        (
            CompareSpec.model_validate({"field": field, "values": values})
            for field, values in compared.items()
            if field in _COMPARABLE and 2 <= len(values) <= 4
        ),
        None,
    )
    filters = plan.filters
    return AppliedFilters(
        drug_name=filtered["drug_name"],
        condition=filtered["condition"],
        sponsor=filtered["sponsor"],
        country=filtered["country"],
        term=filtered["term"],
        trial_phase=list(filters.phases),
        status=list(filters.statuses),
        study_type=list(filters.study_types),
        sponsor_class=list(filters.sponsor_classes),
        intervention_type=list(filters.intervention_types),
        start_year=filters.year_from,
        end_year=filters.year_to,
        date_field=filters.date_field,
        compare=compare,
    )
