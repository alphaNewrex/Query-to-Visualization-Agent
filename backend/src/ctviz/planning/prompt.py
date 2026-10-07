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
    AgeGroup,
    Aggregate,
    Allocation,
    Clarify,
    ClosedDimension,
    DimensionKey,
    Entity,
    FilterEvidence,
    InterventionModel,
    InterventionType,
    Masking,
    Network,
    Phase,
    PrimaryPurpose,
    QueryPlan,
    ResultsPosted,
    Sex,
    SponsorClass,
    Status,
    StudyType,
    Total,
    TrialList,
)
from ctviz.contract.request import QueryRequest
from ctviz.planning.structured import NO_FILTERS

PROMPT_VERSION: Final = "plan-v7"

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
   conditions, sponsors or countries, give each side role "compare" (two to five sides). A class
   of drugs such as "GLP-1 receptor agonists" is one drug entity, written as in the question.
5. Filter lists stay empty unless the question restricts them. Listing every value is the same
   as no filter: leave the list empty. For every non-empty filter list add one filters.evidence
   item that quotes the words of the question stating it. If the question names a value that is
   not in a list (for example "Phase 5"), do not pick a similar one: use "clarify". Phases the
   question names as the ones to show ("compare Phase 1, Phase 2 and Phase 3", "Phase 2 from
   Phase 3") are also a filter: list exactly those in filters.phases, each quoted in evidence.
6. "recruiting" means statuses ["RECRUITING"]. "since 2015" means year_from 2015. Use only
   years the user wrote or that follow from a phrase such as "the last five years".
7. Choose one analysis:
   aggregate    count trials by one dimension (a category, a date with time_unit, or enrollment
                size), optionally split by a second closed dimension in `series`. With compared
                names the split is kept: one line or bar per name and split value;
   total        the question asks for one number with no breakdown ("how many recruiting trials
                are there for X?"). "How many ... each year", "per phase" or "by country" asks
                for a breakdown and is aggregate. With compared names it gives one number each;
   relate       one point per trial for two numeric fields;
   network      how two kinds of thing are connected; use the same kind twice for co-occurrence
                ("which drugs are used together"), with link "same_arm" for combinations;
   trial_list   the largest, latest or earliest N individual trials;
   clarify      a needed name, or what to show, is missing;
   unsupported  not about registered clinical trials; needs data the registry does not hold
                (efficacy, prices, predictions, opinions); asks about one trial by NCT ID; or
                needs a grouping that is not in the glossary. Never use it for an extra that
                only concerns how the chart looks (rule 15) or for a "top N versus the rest"
                split (rule 16): plan the part of the question that can be counted. Never
                use it because the question asks for a share, percentage or proportion: the
                service adds shares to every count it draws;
   converse     the whole message is conversation with the service and asks nothing about
                trials (rule 18).
8. top_n, limit, time_unit and chart_preference are null unless the question asks for them.
9. interpretation is one sentence restating what will be counted and how it is grouped. It
   contains no figures other than those in the question.
10. Leaving out: "excluding X", "without X", "not involving X", "other than X" about a drug,
    condition, sponsor, country or term is an entity with role "exclude", value copied from the
    question; never give it role "filter". Leaving out statuses ("exclude terminated and withdrawn
    studies") is filters.exclude_statuses (the codes of statuses), with a filters.evidence item of family
    "exclude_statuses" that quotes the words. Statuses to leave out are never listed in `statuses`.
11. Compared names of one kind (drugs, countries, sponsors, conditions) are never also the
    dimension: "compare A, B and C by phase" is dimension phase with each name compared, and
    "compare A, B and C per year, separately for each phase" is dimension start_date with series
    phase. Never put the compared kind in `dimension` or `series`. With nothing to break the
    names down by ("how many trials for A vs B"), use analysis total.
12. Grouping words map to the glossary below. "by intervention name", "by intervention", "by drug"
    and "by treatment" are dimension drug; "by sponsor" is sponsor; "by year" is a date dimension
    with time_unit year; "parallel, crossover, factorial or sequential" is dimension
    intervention_model.
13. Every closed dimension in the glossary is also a filter, listed in the enum codes below:
    "randomized" is allocations ["RANDOMIZED"], "double-blind" is maskings ["DOUBLE"], "prevention
    trials" is primary_purposes ["PREVENTION"], "with posted results" is has_results ["true"],
    "children" is age_groups ["CHILD"], "crossover trials" is intervention_models ["CROSSOVER"].
    Words that a filter covers are never a term entity. Each is quoted in filters.evidence with the
    family name of its list (allocations, maskings, sexes, ...).
14. A median, an average or a total of a number is a statistic on aggregate or total: statistic
    "median", "mean" ("average") or "sum" ("total") with `of` enrollment (planned enrollment,
    participants), duration_months (study duration, from start to completion) or site_count (number
    of sites or locations per trial). "Average number of sites per trial by sponsor class" is aggregate
    with dimension sponsor_class, statistic mean, of site_count. "Median duration for A, B and C" is
    total with A, B and C compared. Counting trials leaves statistic and of null. Never "sum" of
    duration_months. Two or more different measures in one question are not supported: use
    "unsupported" with category analysis_not_supported.
15. Extras: wording that asks to make part of the chart look different (highlighting, emphasising,
    marking or annotating some bars or points, a colour, a threshold line) cannot be drawn and does
    not change what is counted. Plan the rest of the question as usual and copy the words of the
    extra, verbatim, into `unapplied`. The service then draws the chart and says that the extra was
    not applied. Nothing else goes into `unapplied`: the service always counts a trial once per
    group however many sites or arms it has, so a request to count each trial once is already met;
    nor is a wish for a clear or informative chart, or anything the plan already expresses. It is
    empty for most questions.
16. "The top N X versus everyone else", "N largest X and all the others", "share held by the top N
    X": analysis aggregate with dimension X (the kind of thing ranked: sponsor, country, drug,
    condition) and top_n N. The service puts all other items into one "Other" bar. The ranked kind is
    the dimension, never an entity with role compare, and the question is not unsupported.
17. Follow-ups. When the user message has "Previous question" and "Previous plan" lines, the
    Question line is the user's new message in a conversation. Decide what it is:
    a refinement of the previous question (it changes one or more parts of it: another drug,
    condition or sponsor in place of or besides the one before; another grouping or split; a
    different period, status, phase or other filter; a number to show; another chart form;
    something to leave out) or a new question that has nothing to do with the previous one.
    Either way return one complete plan that stands on its own.
    - Refinement: start from the previous plan and change only what the message changes. Copy
      everything else unchanged: entities with their roles, every filter list with its evidence,
      years, dimension, series, time_unit, top_n and chart_preference. "Instead" replaces the
      value of the same kind; "also" or "and" adds one; a request to drop or leave out a filter
      removes it. An analysis that no longer fits (a single count asked to be split) changes
      kind as the message requires.
    - New question: ignore the previous plan completely and plan as for a first question.
    - Previous plan of kind clarify: the user is answering it. Take what the previous question
      wanted to know and complete it with the names or choices the message supplies.
    A new name, year, number or filter must still be stated in the Question line. A value can
    stay from the previous plan only when it is copied from it unchanged. Never take a value
    from the previous interpretation text, and never invent one to fill a gap. Evidence for a
    filter that the message newly states quotes the message; evidence for a carried filter
    stays as in the previous plan.
18. Conversation. A message that only greets, thanks, asks who or what the service is or what it
    can do, or is other small talk, is analysis "converse" with a topic: greeting ("hi", "good
    morning"), thanks ("thanks", "great, thank you"), capabilities ("what can you do?", "who are
    you?", "help"), small_talk (any other chat that asks nothing about trials). Use empty
    entities and empty filters. A message that also asks a question about trials ("hi, how many
    trials are there for X?") is that question: plan it and ignore the greeting. A question that is
    not about trials and is not conversation ("what is the capital of France?", a request for
    advice or a calculation) is "unsupported" with category not_about_clinical_trials, never
    converse. Conversation never refines a previous plan.
"""

_CLOSED: Final = frozenset(get_args(ClosedDimension))
_DATES: Final = frozenset({"start_date", "primary_completion_date", "completion_date", "first_posted_date"})
_OPEN: Final = frozenset({"country", "state", "sponsor", "drug", "condition"})
_ENUMS: Final[dict[str, tuple[str, ...]]] = {
    "phases": get_args(Phase),
    "statuses": get_args(Status),
    "study_types": get_args(StudyType),
    "sponsor_classes": get_args(SponsorClass),
    "intervention_types": get_args(InterventionType),
    "sexes": get_args(Sex),
    "age_groups": get_args(AgeGroup),
    "allocations": get_args(Allocation),
    "maskings": get_args(Masking),
    "primary_purposes": get_args(PrimaryPurpose),
    "has_results": get_args(ResultsPosted),
    "intervention_models": get_args(InterventionModel),
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
    hints = {spec.key: spec.hint for spec in catalog.values() if spec.hint}
    return "\n".join(
        f"  {key}: {title} ({kind})" + (f": {hints[key]}" if key in hints else "")
        for key, title, kind in entries
    )


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
    analysis: Aggregate | Total | Network | TrialList | Clarify,
    *,
    filters: dict[str, object] | None = None,
    unapplied: list[str] | None = None,
) -> QueryPlan:
    return QueryPlan(
        interpretation=interpretation,
        entities=entities,
        filters=NO_FILTERS.model_copy(update=filters or {}),
        analysis=analysis,
        chart_preference=None,
        unapplied=unapplied or [],
    )


def _entity(kind: str, value: str, role: str = "filter") -> Entity:
    return Entity.model_validate({"kind": kind, "value": value, "role": role})


def _aggregate(dimension: str, *, time_unit: str | None = None) -> Aggregate:
    return Aggregate.model_validate(
        {
            "kind": "aggregate",
            "dimension": dimension,
            "series": None,
            "time_unit": time_unit,
            "top_n": None,
            "statistic": None,
            "of": None,
        }
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
        QueryRequest(
            query="Compare secukinumab, ixekizumab and guselkumab trials for psoriasis per year since 2018, "
            "separately for each phase, excluding withdrawn studies."
        ),
        _plan(
            "Count psoriasis trials per start year since 2018 for each of three drugs, split by phase, "
            "leaving out withdrawn studies.",
            [
                _entity("condition", "psoriasis"),
                _entity("drug", "secukinumab", "compare"),
                _entity("drug", "ixekizumab", "compare"),
                _entity("drug", "guselkumab", "compare"),
            ],
            Aggregate.model_validate(
                {
                    "kind": "aggregate",
                    "dimension": "start_date",
                    "series": "phase",
                    "time_unit": "year",
                    "top_n": None,
                    "statistic": None,
                    "of": None,
                }
            ),
            filters={
                "year_from": 2018,
                "exclude_statuses": ["WITHDRAWN"],
                "evidence": [FilterEvidence(family="exclude_statuses", phrase="excluding withdrawn studies")],
            },
        ),
    ),
    Example(
        QueryRequest(query="Compare Japan, South Korea and Brazil by phase for hypertension trials."),
        _plan(
            "Count hypertension trials by phase for each of three countries.",
            [
                _entity("condition", "hypertension"),
                _entity("country", "Japan", "compare"),
                _entity("country", "South Korea", "compare"),
                _entity("country", "Brazil", "compare"),
            ],
            _aggregate("phase"),
        ),
    ),
    Example(
        QueryRequest(query="What is the average enrollment of asthma trials in each sponsor class?"),
        _plan(
            "Compute the mean enrollment of asthma trials for each sponsor class.",
            [_entity("condition", "asthma")],
            Aggregate.model_validate(
                {
                    "kind": "aggregate",
                    "dimension": "sponsor_class",
                    "series": None,
                    "time_unit": None,
                    "top_n": None,
                    "statistic": "mean",
                    "of": "enrollment",
                }
            ),
        ),
    ),
    Example(
        QueryRequest(
            query="Compare the median study duration of completed trials for secukinumab, ixekizumab and "
            "guselkumab."
        ),
        _plan(
            "Compute the median duration of completed trials for each of three drugs.",
            [
                _entity("drug", "secukinumab", "compare"),
                _entity("drug", "ixekizumab", "compare"),
                _entity("drug", "guselkumab", "compare"),
            ],
            Total.model_validate({"kind": "total", "statistic": "median", "of": "duration_months"}),
            filters={
                "statuses": ["COMPLETED"],
                "evidence": [FilterEvidence(family="statuses", phrase="completed")],
            },
        ),
    ),
    Example(
        QueryRequest(
            query="What share of recruiting gout trials is held by the five biggest sponsors compared with "
            "all other sponsors?"
        ),
        _plan(
            "Count recruiting gout trials by lead sponsor: the five largest sponsors, the rest as Other.",
            [_entity("condition", "gout")],
            Aggregate.model_validate(
                {
                    "kind": "aggregate",
                    "dimension": "sponsor",
                    "series": None,
                    "time_unit": None,
                    "top_n": 5,
                    "statistic": None,
                    "of": None,
                }
            ),
            filters={
                "statuses": ["RECRUITING"],
                "evidence": [FilterEvidence(family="statuses", phrase="recruiting")],
            },
        ),
    ),
    Example(
        QueryRequest(
            query="Show the distribution of enrollment sizes for Phase 2 eczema trials and mark the biggest "
            "ones in red."
        ),
        _plan(
            "Count Phase 2 eczema trials by planned enrollment size.",
            [_entity("condition", "eczema")],
            _aggregate("enrollment"),
            filters={
                "phases": ["PHASE2"],
                "evidence": [FilterEvidence(family="phases", phrase="Phase 2")],
            },
            unapplied=["mark the biggest ones in red"],
        ),
    ),
    Example(
        QueryRequest(
            query="Compare ustekinumab and vedolizumab trials for Crohn's disease per year. Count each trial "
            "once even if it has many sites, and make the chart easy to read."
        ),
        _plan(
            "Count Crohn's disease trials per start year for ustekinumab and for vedolizumab.",
            [
                _entity("condition", "Crohn's disease"),
                _entity("drug", "ustekinumab", "compare"),
                _entity("drug", "vedolizumab", "compare"),
            ],
            _aggregate("start_date", time_unit="year"),
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
    """The dynamic part of the prompt: today's date, the fields, the previous turn and the question."""
    fields = request.model_dump(mode="json", exclude_none=True, exclude={"query", "options", "previous"})
    structured = json.dumps(fields) if fields else "none"
    previous = request.previous
    turn = (
        f"Previous question: {previous.query or 'none'}\nPrevious plan: {previous.plan.model_dump_json()}\n"
        if previous is not None
        else ""
    )
    return f"Today: {today.isoformat()}\nStructured fields: {structured}\n{turn}Question: {request.query}"


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
