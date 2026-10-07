"""`check_plan`: the pure checks that turn a plan into a canonical, grounded one before any data is fetched.

Order of execution, as in section 4.5 of the plan: text hygiene (rule 1); merging the structured request
fields (rules 3 to 5); the direct outcomes (24 to 27); the fixes (2 and 6 to 17); the blocking rules
(18 to 23); then, once the one repair turn has been spent, rules 28 and 29.
"""

from dataclasses import dataclass
from datetime import date
from typing import Final, cast

from ctviz.contract.plan import Aggregate, Clarify, FilterFamily, Network, PlanIssue, QueryPlan, TrialList
from ctviz.contract.request import QueryRequest
from ctviz.contract.response import Adjustment, Note, Outcome
from ctviz.planning.findings import Findings
from ctviz.planning.grounding import ENTITY_FIELDS, CountryTable, Facts, Mode, tokens
from ctviz.planning.merge import merge_request
from ctviz.planning.outcomes import could_not_interpret, direct_outcome, outcome_of_analysis, unsupported
from ctviz.planning.rules import (
    DATE_DIMENSIONS,
    apply_fixes,
    drop_family,
    find_blocking,
    fix_text_hygiene,
)

DEFAULT_TOP_N: Final = 15
DEFAULT_LIMIT: Final = 10


@dataclass(frozen=True)
class PlanCheck:
    """The canonical plan and everything the checks found.

    `blocking` is what only the model can repair; `outcome` is an answer decided without any data.
    """

    plan: QueryPlan
    adjustments: tuple[Adjustment, ...]
    warnings: tuple[Note, ...]
    blocking: tuple[PlanIssue, ...]
    outcome: Outcome | None


def check_plan(
    plan: QueryPlan,
    request: QueryRequest | None,
    *,
    mode: Mode,
    countries: CountryTable | None,
    today: date,
    after_repair: bool = False,
) -> PlanCheck:
    """Check a plan; `after_repair` says the repair turn is spent, so rules 28 and 29 replace repair."""
    facts = Facts.of(mode, request, today, countries)
    found = Findings()
    if mode == "model":
        plan = fix_text_hygiene(plan, facts, found)
        if request is not None:
            plan = merge_request(plan, request, found)
            _block_unused_fields(plan, request, found)
    plan = _drop_duplicate_entities(plan, found)
    plan, outcome = direct_outcome(plan, facts, found)
    if outcome is None:
        plan = apply_fixes(plan, facts, found)
        if not found.blocking:
            outcome = outcome_of_analysis(plan)
        if outcome is None:
            find_blocking(plan, facts, found)
    if after_repair and found.blocking:
        plan, outcome = _settle_unrepaired(plan, found)
    if outcome is None and not found.blocking:
        plan = _with_defaults(plan)
    return PlanCheck(plan, tuple(found.adjustments), tuple(found.warnings), tuple(found.blocking), outcome)


def _block_unused_fields(plan: QueryPlan, request: QueryRequest, found: Findings) -> None:
    """Rule 4, second part: a `clarify` that asks for what the request already supplies has no meaning.

    Code cannot tell which analysis was meant, so the model is asked again.
    """
    analysis = plan.analysis
    if not isinstance(analysis, Clarify) or analysis.reason != "missing_entity" or not analysis.missing:
        return
    supplied = {name for name in ENTITY_FIELDS if getattr(request, name)}
    supplied |= {name for name in ("compare", "group_by") if getattr(request, name) is not None}
    if supplied.issuperset(analysis.missing):
        found.block(
            "field_override",
            "/analysis",
            f"The request already supplies {', '.join(analysis.missing)}. Use it and choose an analysis.",
        )


def _drop_duplicate_entities(plan: QueryPlan, found: Findings) -> QueryPlan:
    """Rule 3: the same kind and words twice."""
    seen: set[tuple[str, tuple[str, ...]]] = set()
    kept = []
    for index, entity in enumerate(plan.entities):
        key = (entity.kind, tokens(entity.value))
        if key in seen:
            found.adjust(
                "duplicate_entity", f"/entities/{index}", "The same name was listed twice.", "dropped"
            )
        else:
            seen.add(key)
            kept.append(entity)
    return plan.model_copy(update={"entities": kept})


def _settle_unrepaired(plan: QueryPlan, found: Findings) -> tuple[QueryPlan, Outcome | None]:
    """Rules 28 and 29: what the repair turn did not fix is dropped (a filter) or becomes an outcome."""
    remaining = []
    for issue in found.blocking:
        if issue.code != "ungrounded_filter":
            remaining.append(issue)
            continue
        family = cast(FilterFamily, issue.path.rsplit("/", 1)[1])
        plan = drop_family(plan, family)
        found.adjust("filter_dropped", issue.path, f"No words of the question state {family}.", "dropped")
        found.warn(
            "filter_dropped", f"The {family} filter was left out because the question does not state it."
        )
    found.blocking.clear()
    if not remaining:
        return plan, None
    codes = ", ".join(issue.code for issue in remaining)
    found.warn("plan_not_repaired", f"The plan still had problems after one repair: {codes}.")
    if any(issue.code == "network_pair_unsupported" for issue in remaining):
        return plan, unsupported("analysis_not_supported")
    return plan, could_not_interpret()


def _with_defaults(plan: QueryPlan) -> QueryPlan:
    """Write the defaults into the plan so that the echoed `meta.plan` states them; this is no fix."""
    analysis = plan.analysis
    if isinstance(analysis, Clarify) or analysis.kind == "unsupported":
        return plan
    if plan.filters.date_field is None:
        is_dated = isinstance(analysis, Aggregate) and analysis.dimension in DATE_DIMENSIONS
        date_field = analysis.dimension if isinstance(analysis, Aggregate) and is_dated else "start_date"
        plan = plan.model_copy(update={"filters": plan.filters.model_copy(update={"date_field": date_field})})
    defaults: dict[str, object] = {}
    if isinstance(analysis, Aggregate):
        if analysis.dimension in DATE_DIMENSIONS and analysis.time_unit is None:
            defaults["time_unit"] = "year"
        if analysis.dimension not in DATE_DIMENSIONS | {"enrollment"} and analysis.top_n is None:
            defaults["top_n"] = DEFAULT_TOP_N
    elif isinstance(analysis, Network) and analysis.link is None:
        defaults["link"] = (
            "same_arm" if (analysis.source, analysis.target) == ("drug", "drug") else "same_trial"
        )
    elif isinstance(analysis, TrialList) and analysis.limit is None:
        defaults["limit"] = DEFAULT_LIMIT
    return plan.model_copy(update={"analysis": analysis.model_copy(update=defaults)}) if defaults else plan
