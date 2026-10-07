"""The planner's instructions and the user message: static prefix, dynamic suffix.

The instructions are static text so that the provider can cache the prefix. Today's date, the structured
fields and the question go in the user message.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Final, get_args

from ctviz.catalog.fields import CATALOG, FieldSpec
from ctviz.contract.plan import (
    Aggregate,
    Clarify,
    ClosedDimension,
    DimensionKey,
    Entity,
    FilterEvidence,
    InterventionType,
    Network,
    Phase,
    QueryPlan,
    SponsorClass,
    Status,
    StudyType,
    TrialList,
)
from ctviz.contract.request import QueryRequest
from ctviz.planning.structured import NO_FILTERS

PROMPT_VERSION: Final = "plan-v1"

RULES: Final = """\
You translate a question about clinical trials into a query plan for a service that counts
ClinicalTrials.gov registry records and draws one chart. You never answer the question yourself
and you never write counts, trial names or identifiers.

1. Record only what the question or the structured fields state. Never add a drug, condition,
   sponsor, country, phase, status, year or number that is not there.
2. entities[].value is copied character for character from the question or from a structured
   field. Do not translate brand names, expand abbreviations or fix spelling.
3. "this drug", "this condition", "[drug]" and similar refer to the structured fields. If no
   structured field supplies the name, use analysis "clarify". Placeholders such as "Drug A",
   "[condition]" or "two conditions" are not names.
4. An entity every counted trial must match has role "filter". For "A vs B" about named drugs,
   conditions, sponsors or countries, give each side role "compare".
5. Filter lists stay empty unless the question restricts them. Listing every value is the same
   as no filter: leave the list empty. For every non-empty filter list add one filters.evidence
   item that quotes the words of the question stating it. If the question names a value that is
   not in a list (for example "Phase 5"), do not pick a similar one: use "clarify".
6. "recruiting" means statuses ["RECRUITING"]. "since 2015" means year_from 2015. Use only
   years the user wrote or that follow from a phrase such as "the last five years".
7. Choose one analysis:
   aggregate    count trials by one dimension (a category, a date with time_unit, or enrollment
                size), optionally split by a second closed dimension in `series`;
   total        the question asks for one number with no breakdown ("how many recruiting trials
                are there for X?"). "How many ... each year", "per phase" or "by country" asks
                for a breakdown and is aggregate;
   relate       one point per trial for two numeric fields;
   network      how two kinds of thing are connected; use the same kind twice for co-occurrence
                ("which drugs are used together"), with link "same_arm" for combinations;
   trial_list   the largest, latest or earliest N individual trials;
   clarify      a needed name, or what to show, is missing;
   unsupported  not about registered clinical trials; needs data the registry does not hold
                (efficacy, prices, predictions, opinions); asks about one trial by NCT ID; or
                needs a grouping that is not in the glossary.
8. top_n, limit, time_unit and chart_preference are null unless the question asks for them.
9. interpretation is one sentence restating what will be counted and how it is grouped. It
   contains no figures other than those in the question.
"""

_CLOSED: Final = frozenset(get_args(ClosedDimension))
_DATES: Final = frozenset({"start_date", "primary_completion_date", "completion_date", "first_posted_date"})
_OPEN: Final = frozenset({"country", "sponsor", "drug", "condition"})
_ENUMS: Final[dict[str, tuple[str, ...]]] = {
    "phases": get_args(Phase),
    "statuses": get_args(Status),
    "study_types": get_args(StudyType),
    "sponsor_classes": get_args(SponsorClass),
    "intervention_types": get_args(InterventionType),
}
# Codes whose label is not the code in sentence case.
_LABELS: Final = {
    "EARLY_PHASE1": "Early Phase 1",
    "NA": "Not Applicable",
    **{f"PHASE{n}": f"Phase {n}" for n in range(1, 5)},
    "NIH": "NIH",
    "FED": "Federal",
    "OTHER_GOV": "Other government",
    "INDIV": "Individual",
    "AMBIG": "Ambiguous",
}
_EXAMPLE_DATE: Final = date(2026, 1, 15)


def _kind_of(key: str) -> str:
    if key in _CLOSED:
        return "category"
    if key in _OPEN:
        return "entity"
    return "date" if key in _DATES else "number"


def render_glossary(catalog: Mapping[str, FieldSpec] = CATALOG) -> str:
    """One line per dimension: key, what it counts by, and its kind.

    Taken from the field catalogue; while the catalogue is empty, from the `DimensionKey` vocabulary.
    """
    entries: list[tuple[str, str, str]]
    if catalog:
        entries = [(spec.key, spec.title, spec.kind) for spec in catalog.values()]
    else:
        entries = [(key, key.replace("_", " "), _kind_of(key)) for key in get_args(DimensionKey)]
    return "\n".join(f"  {key}: {title} ({kind})" for key, title, kind in entries)


def _label(code: str) -> str:
    return _LABELS.get(code, code.replace("_", " ").capitalize())


def render_enums() -> str:
    return "\n".join(
        f"  {family}: " + ", ".join(f"{code} ({_label(code)})" for code in codes)
        for family, codes in _ENUMS.items()
    )


@dataclass(frozen=True)
class Example:
    request: QueryRequest
    plan: QueryPlan


def _plan(
    interpretation: str,
    entities: list[Entity],
    analysis: Aggregate | Network | TrialList | Clarify,
    *,
    filters: dict[str, object] | None = None,
) -> QueryPlan:
    return QueryPlan(
        interpretation=interpretation,
        entities=entities,
        filters=NO_FILTERS.model_copy(update=filters or {}),
        analysis=analysis,
        chart_preference=None,
    )


def _entity(kind: str, value: str, role: str = "filter") -> Entity:
    return Entity.model_validate({"kind": kind, "value": value, "role": role})


def _aggregate(dimension: str, *, time_unit: str | None = None) -> Aggregate:
    return Aggregate.model_validate(
        {"kind": "aggregate", "dimension": dimension, "series": None, "time_unit": time_unit, "top_n": None}
    )


def _network(source: str, target: str, link: str | None = None) -> Network:
    return Network.model_validate({"kind": "network", "source": source, "target": target, "link": link})


# None of these names occurs in the evaluation questions: a test asserts it.
EXAMPLES: Final = (
    Example(
        QueryRequest(query="How has the number of semaglutide trials changed per year since 2018?"),
        _plan(
            "Count semaglutide trials by start year from 2018.",
            [_entity("drug", "semaglutide")],
            _aggregate("start_date", time_unit="year"),
            filters={"year_from": 2018},
        ),
    ),
    Example(
        QueryRequest(query="How are asthma trials distributed across phases?"),
        _plan("Count asthma trials by phase.", [_entity("condition", "asthma")], _aggregate("phase")),
    ),
    Example(
        QueryRequest(query="Compare phases for trials involving adalimumab vs etanercept."),
        _plan(
            "Count trials by phase for adalimumab and for etanercept side by side.",
            [_entity("drug", "adalimumab", "compare"), _entity("drug", "etanercept", "compare")],
            _aggregate("phase"),
        ),
    ),
    Example(
        QueryRequest(query="Which countries have the most recruiting trials for psoriasis?"),
        _plan(
            "Count recruiting psoriasis trials by country.",
            [_entity("condition", "psoriasis")],
            _aggregate("country"),
            filters={
                "statuses": ["RECRUITING"],
                "evidence": [FilterEvidence(family="statuses", phrase="recruiting")],
            },
        ),
    ),
    Example(
        QueryRequest(query="Show a network of sponsors and drugs for Parkinson's disease trials."),
        _plan(
            "Link sponsors to drugs across Parkinson's disease trials.",
            [_entity("condition", "Parkinson's disease")],
            _network("sponsor", "drug"),
        ),
    ),
    Example(
        QueryRequest(query="Which drugs are most often given together with bevacizumab?"),
        _plan(
            "Link drugs that share an arm with bevacizumab.",
            [_entity("drug", "bevacizumab")],
            _network("drug", "drug", "same_arm"),
        ),
    ),
    Example(
        QueryRequest(query="List the ten largest hypertension trials."),
        _plan(
            "List hypertension trials by enrollment, largest first.",
            [_entity("condition", "hypertension")],
            TrialList.model_validate(
                {"kind": "trial_list", "sort_by": "enrollment", "order": "desc", "limit": 10}
            ),
        ),
    ),
    Example(
        QueryRequest(query="How has the number of trials for this drug changed over time?"),
        _plan(
            "Count trials for an unnamed drug by start year.",
            [],
            Clarify(kind="clarify", reason="missing_entity", missing=["drug_name"]),
        ),
    ),
)


def user_message(request: QueryRequest, today: date) -> str:
    """The dynamic part of the prompt: today's date, the structured fields and the question."""
    fields = request.model_dump(mode="json", exclude_none=True, exclude={"query", "options"})
    structured = json.dumps(fields) if fields else "none"
    return f"Today: {today.isoformat()}\nStructured fields: {structured}\nQuestion: {request.query}"


def render_examples() -> str:
    return "\n\n".join(
        f"Example {number}\n{user_message(example.request, _EXAMPLE_DATE)}\n"
        f"Plan: {example.plan.model_dump_json()}"
        for number, example in enumerate(EXAMPLES, start=1)
    )


def build_instructions(catalog: Mapping[str, FieldSpec] = CATALOG) -> str:
    """The full static instructions: rules, glossary, enum codes and the worked examples."""
    return (
        f"{RULES}\n"
        f"Glossary of dimensions (dimension and series values):\n{render_glossary(catalog)}\n\n"
        f"Enum codes for filters:\n{render_enums()}\n\n"
        f"Worked examples:\n\n{render_examples()}\n"
    )
