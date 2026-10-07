"""The scope of a plan as request fields, so that `meta.filters` can be sent back as a request."""

from collections import defaultdict
from typing import Final, get_args

from ctviz.contract.plan import QueryPlan
from ctviz.contract.request import CompareSpec
from ctviz.contract.response import AppliedExclusions, AppliedFilters

_FIELD_OF_KIND: Final = {
    "drug": "drug_name",
    "condition": "condition",
    "sponsor": "sponsor",
    "country": "country",
    "term": "term",
}
MAX_COMPARED: Final = 5
_COMPARABLE: Final = frozenset(get_args(CompareSpec.model_fields["field"].annotation))


def applied_filters(plan: QueryPlan) -> AppliedFilters:
    """Filter entities and enum filters as arrays, the comparison as `compare`."""
    filtered: defaultdict[str, list[str]] = defaultdict(list)
    compared: defaultdict[str, list[str]] = defaultdict(list)
    excluded: defaultdict[str, list[str]] = defaultdict(list)
    groups = {"filter": filtered, "compare": compared, "exclude": excluded}
    for entity in plan.entities:
        groups[entity.role][_FIELD_OF_KIND[entity.kind]].append(entity.value)
    compare = next(
        (
            CompareSpec.model_validate({"field": field, "values": values})
            for field, values in compared.items()
            if field in _COMPARABLE and 2 <= len(values) <= MAX_COMPARED
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
        sex=list(filters.sexes),
        age_group=list(filters.age_groups),
        allocation=list(filters.allocations),
        masking=list(filters.maskings),
        primary_purpose=list(filters.primary_purposes),
        has_results=list(filters.has_results),
        intervention_model=list(filters.intervention_models),
        start_year=filters.year_from,
        end_year=filters.year_to,
        date_field=filters.date_field,
        compare=compare,
        exclude=AppliedExclusions(
            drug_name=excluded["drug_name"],
            condition=excluded["condition"],
            sponsor=excluded["sponsor"],
            country=excluded["country"],
            term=excluded["term"],
            status=list(filters.exclude_statuses),
        ),
    )
