"""Answers decided before any data is fetched: a clarification or an unsupported question.

All text here is a template. Nothing the model wrote reaches `message`.
"""

import re
from collections.abc import Sequence
from typing import Final

from ctviz.catalog.fields import CATALOG
from ctviz.contract.plan import Clarify, Converse, PlanIssue, QueryPlan, Unsupported
from ctviz.contract.response import Clarification, ClarificationReason, LabeledRequest, Outcome
from ctviz.planning.findings import Findings
from ctviz.planning.grounding import (
    ENTITY_FIELDS,
    Facts,
    is_placeholder,
    request_words,
    same_words,
    unknown_phase_mentions,
)

_NCT_ID: Final = re.compile(r"NCT\d{8}", re.IGNORECASE)
_MAX_SUGGESTIONS: Final = 3
_FIELD_OF_KIND: Final = {kind: name for name, kind in ENTITY_FIELDS.items() if kind != "term"}
_FIELD_LABELS: Final = {
    "drug_name": "drug name",
    "condition": "condition",
    "sponsor": "sponsor",
    "country": "country",
    "compare": "pair of things to compare",
    "group_by": "grouping",
    "trial_phase": "phase",
}
_UNSUPPORTED_MESSAGES: Final = {
    "not_about_clinical_trials": "This question is not about clinical trials registered on "
    "ClinicalTrials.gov, "
    "so there are no registry records to count for it.",
    "needs_data_not_in_registry": "The question needs data that ClinicalTrials.gov does not hold "
    "(results such "
    "as efficacy, prices, predictions or opinions). The registry describes how trials are designed and run.",
    "single_trial_lookup": "The question is about one trial. This service charts groups of trials: open the "
    "trial on ClinicalTrials.gov, or ask how many trials match a description.",
    "analysis_not_supported": "The question needs a grouping, measure or comparison that this "
    "service does not "
    "offer. {offered}",
    "other": "This question cannot be answered by this service.",
}
_SERVICE: Final = (
    "This service answers questions about clinical trials registered on ClinicalTrials.gov "
    "with a chart, a number or a table."
)
DECLINED_MESSAGE: Final = "The planning model declined this question, so nothing was drawn."
COULD_NOT_INTERPRET_MESSAGE: Final = (
    "The question could not be turned into a checked plan, so nothing was drawn. "
    "Rephrase it, or use the structured fields."
)


def clarification(
    reason: ClarificationReason,
    message: str,
    missing_fields: Sequence[str] = (),
    options: Sequence[LabeledRequest] = (),
) -> Outcome:
    detail = Clarification(reason=reason, missing_fields=list(missing_fields), options=list(options))
    return Outcome(kind="clarification", reason=reason, message=message, clarification=detail)


def _offered() -> str:
    """What the service can group by and measure, from the field catalogue, for the unsupported message."""
    titles = ", ".join(spec.title.lower() for spec in CATALOG.values())
    return (
        f"It counts trials by {titles}, or gives the median, mean or sum of enrollment, duration or number "
        "of sites, optionally split by one more category."
    )


def unsupported(category: str) -> Outcome:
    """The answer for a plan marked unsupported: a sentence per category, written here, not by the model."""
    message = _UNSUPPORTED_MESSAGES.get(category, _UNSUPPORTED_MESSAGES["other"])
    return Outcome(kind="unsupported", reason=category, message=message.format(offered=_offered()).strip())


def conversation(topic: str) -> Outcome:
    """A reply to a greeting, thanks or a question about the service: a template per topic, no data fetched.

    Suggested follow-ups are attached by the pipeline, which knows where the examples live.
    """
    if topic == "thanks":
        message = "You are welcome. Ask another question about clinical trials whenever you like."
    elif topic == "capabilities":
        message = f"{_SERVICE} {_offered()}"
    elif topic == "small_talk":
        message = f"{_SERVICE} Ask a question about trials to get started."
    else:
        message = f"Hello! {_SERVICE}"
    return Outcome(kind="conversation", reason=topic, message=message)


def could_not_interpret(issues: Sequence[PlanIssue] = ()) -> Outcome:
    """A plan that stayed blocked: the sentences code wrote for each problem say what was wrong."""
    reasons = " ".join(dict.fromkeys(issue.message for issue in issues))
    message = (
        COULD_NOT_INTERPRET_MESSAGE if not reasons else f"{COULD_NOT_INTERPRET_MESSAGE} Problem: {reasons}"
    )
    return clarification("could_not_interpret", message)


def outcome_of_analysis(plan: QueryPlan) -> Outcome | None:
    """The outcome a plan states by its own analysis: `clarify` or `unsupported`."""
    analysis = plan.analysis
    if isinstance(analysis, Unsupported):
        return unsupported(analysis.category)
    if isinstance(analysis, Converse):
        return conversation(analysis.topic)
    if isinstance(analysis, Clarify):
        return clarification(analysis.reason, _clarify_message(analysis), analysis.missing)
    return None


def _clarify_message(analysis: Clarify) -> str:
    if analysis.reason == "ambiguous_request":
        return "The question can be read in more than one way. Say what to count and how to group it."
    names = " and ".join(_FIELD_LABELS[name] for name in analysis.missing)
    if not names:
        return "The question does not say what to count. Name it and ask again."
    return f"The question needs a {names} that it does not give. Add it to the question or its field."


def direct_outcome(plan: QueryPlan, facts: Facts, found: Findings) -> tuple[QueryPlan, Outcome | None]:
    """Rules 24 to 27, in that order; the first that fires decides, after a plan marked unsupported.

    Rule 24 rewrites the plan's analysis to `clarify` so that posting the canonical plan to
    `/v1/analyses` reproduces the clarification. In structured and supplied modes rules 25 and 27 have
    no outcome to give: the client is told what is wrong, so they are blocking issues.
    """
    if isinstance(plan.analysis, Unsupported):
        # A plan marked unsupported is answered before any entity check can ask a question.
        return plan, unsupported(plan.analysis.category)
    if isinstance(plan.analysis, Converse):
        return plan, conversation(plan.analysis.topic)
    if facts.mode == "model":
        plan, outcome = _placeholders(plan, facts, found)
        if outcome is not None:
            return plan, outcome
    outcome = _unknown_countries(plan, facts, found)
    if outcome is None and facts.mode == "model":
        outcome = _unknown_phase(facts)
    if outcome is None:
        outcome = _nct_entities(plan, facts, found)
    return plan, outcome


def _placeholders(plan: QueryPlan, facts: Facts, found: Findings) -> tuple[QueryPlan, Outcome | None]:
    given = request_words(facts.request)
    holders = [
        entity
        for entity in plan.entities
        if is_placeholder(entity.value) and not any(same_words(entity.value, word) for word in given)
    ]
    if not holders:
        return plan, None
    missing = list(dict.fromkeys(_FIELD_OF_KIND[e.kind] for e in holders if e.kind in _FIELD_OF_KIND))
    analysis = Clarify(kind="clarify", reason="missing_entity", missing=missing)
    found.adjust(
        "placeholder_entity",
        "/analysis",
        f"A placeholder is not a name; the plan became a {analysis.kind}.",
        "replaced",
    )
    kept = [entity for entity in plan.entities if entity not in holders]
    plan = plan.model_copy(update={"entities": kept, "analysis": analysis})
    return plan, clarification("missing_entity", _clarify_message(analysis), missing)


def _unknown_countries(plan: QueryPlan, facts: Facts, found: Findings) -> Outcome | None:
    if facts.countries is None:
        return None
    for index, entity in enumerate(plan.entities):
        if entity.kind != "country" or facts.countries.resolve(entity.value) is not None:
            continue
        suggestions = tuple(facts.countries.suggest(entity.value))[:_MAX_SUGGESTIONS]
        hint = f" Did you mean {', '.join(suggestions)}?" if suggestions else ""
        message = f"'{entity.value}' is not a country name the registry uses.{hint}"
        if facts.mode != "model":
            found.block("unknown_country", f"/entities/{index}/value", message, suggestions)
            continue
        return clarification("unknown_value", message, ["country"], _country_options(facts, suggestions))
    return None


def _country_options(facts: Facts, suggestions: Sequence[str]) -> list[LabeledRequest]:
    request = facts.request
    if request is None:
        return []
    return [
        LabeledRequest(
            label=f"Use {name}",
            request=request.model_copy(update={"country": [*(request.country or []), name]}),
        )
        for name in suggestions
    ]


def _unknown_phase(facts: Facts) -> Outcome | None:
    spellings = unknown_phase_mentions(facts.question)
    if not spellings:
        return None
    message = (
        f"The registry has phases 1 to 4, early phase 1 and not applicable. '{spellings[0]}' is none of them."
    )
    return clarification("unknown_value", message, ["trial_phase"])


def _nct_entities(plan: QueryPlan, facts: Facts, found: Findings) -> Outcome | None:
    for index, entity in enumerate(plan.entities):
        if _NCT_ID.search(entity.value) is None:
            continue
        if facts.mode != "model":
            found.block(
                "nct_id_entity", f"/entities/{index}/value", "A trial identifier cannot be a search name."
            )
            continue
        return unsupported("single_trial_lookup")
    return None
