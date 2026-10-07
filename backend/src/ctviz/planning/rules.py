"""The plan rules that repair a plan in place (fixes) and the rules that need the model's help (blocking).

Each fix takes a plan and returns the repaired one, recording what it did. The rule numbers are those
of the table in section 4.5 of the plan.
"""

import re
from collections.abc import Sequence
from typing import Final, get_args

from ctviz.contract.plan import (
    Aggregate,
    ChartType,
    FilterFamily,
    InterventionType,
    Network,
    Phase,
    QueryPlan,
    Relate,
    SponsorClass,
    Status,
    StudyType,
    TrialList,
)
from ctviz.ctgov import essie
from ctviz.planning.findings import Findings
from ctviz.planning.grounding import (
    Facts,
    has_relative_time,
    is_grounded,
    mentions_number,
    phase_is_grounded,
    tokens,
)

MAX_LIMIT: Final = 50
MAX_COMPARED: Final = 4
DATE_DIMENSIONS: Final = frozenset(
    {"start_date", "primary_completion_date", "completion_date", "first_posted_date"}
)
# A trial has several values of these, so they cannot split another count into disjoint parts.
MULTI_VALUED: Final = frozenset({"intervention_type", "age_group"})
# A trial has exactly one lead sponsor, so no trial links two sponsors.
SINGLE_VALUED_NODES: Final = frozenset({"sponsor"})
HYGIENE_REPLACEMENT: Final = (
    "The planner's reading of the question was replaced because it broke the text rules."
)

_MAX_TEXT_LENGTH: Final = 300
_NCT_ID: Final = re.compile(r"NCT\d{8}", re.IGNORECASE)
_YEARS_BEFORE_TODAY: Final = 50
_YEARS_AFTER_TODAY: Final = 5
FAMILY_ENUMS: Final[dict[FilterFamily, tuple[str, ...]]] = {
    "phases": get_args(Phase),
    "statuses": get_args(Status),
    "study_types": get_args(StudyType),
    "sponsor_classes": get_args(SponsorClass),
    "intervention_types": get_args(InterventionType),
}


def _filters(plan: QueryPlan, **changes: object) -> QueryPlan:
    return plan.model_copy(update={"filters": plan.filters.model_copy(update=changes)})


def _analysis(plan: QueryPlan, **changes: object) -> QueryPlan:
    return plan.model_copy(update={"analysis": plan.analysis.model_copy(update=changes)})


def drop_family(plan: QueryPlan, family: FilterFamily, keep: Sequence[object] = ()) -> QueryPlan:
    """Set a filter family to `keep` (none by default), together with the evidence that stated it."""
    evidence = [item for item in plan.filters.evidence if item.family != family]
    return _filters(plan, **{family: list(keep), "evidence": evidence})


def _compared(plan: QueryPlan) -> int:
    return sum(entity.role == "compare" for entity in plan.entities)


# --- rule 1 -------------------------------------------------------------------------------------


def fix_text_hygiene(plan: QueryPlan, facts: Facts, found: Findings) -> QueryPlan:
    """Rule 1: model-written text that is long, names a trial or holds a number nobody wrote is replaced."""
    if _is_unclean(plan.interpretation, facts):
        found.adjust(
            "text_hygiene", "/interpretation", "The interpretation broke the text rules.", "replaced"
        )
        plan = plan.model_copy(update={"interpretation": HYGIENE_REPLACEMENT})
    if plan.analysis.kind == "unsupported" and _is_unclean(plan.analysis.reason, facts):
        found.adjust("text_hygiene", "/analysis/reason", "The reason broke the text rules.", "replaced")
        plan = _analysis(plan, reason=HYGIENE_REPLACEMENT)
    return plan


def _is_unclean(text: str, facts: Facts) -> bool:
    digit_runs = {token for token in tokens(text) if token.isdigit()}
    return len(text) > _MAX_TEXT_LENGTH or _NCT_ID.search(text) is not None or not digit_runs <= facts.known


# --- the fixes: rules 2 and 6 to 17 ---------------------------------------------------------------


def apply_fixes(plan: QueryPlan, facts: Facts, found: Findings) -> QueryPlan:
    """Run the fixes in the order of the plan, except that rule 15 runs before rule 14 (see `fix_limits`)."""
    plan = fix_all_values_filters(plan, found)
    plan = fix_empty_entities(plan, found)
    plan = fix_inverted_years(plan, found)
    plan = fix_single_compare(plan, found)
    plan = fix_too_many_compare(plan, found)
    plan = fix_series_with_compare(plan, found)
    plan = fix_series_not_allowed(plan, found)
    plan = fix_time_unit(plan, found)
    plan = fix_link(plan, found)
    plan = fix_limits(plan, facts, found)
    plan = fix_chart_preference(plan, found)
    if facts.mode == "model":
        plan = fix_ungrounded_phases(plan, facts, found)
    return plan


def fix_all_values_filters(plan: QueryPlan, found: Findings) -> QueryPlan:
    """Rule 2: a filter that lists every value of its enum restricts nothing."""
    for family, every_value in FAMILY_ENUMS.items():
        if set(getattr(plan.filters, family)) == set(every_value):
            found.adjust(
                "all_values_filter", f"/filters/{family}", "A list of every value is no filter.", "dropped"
            )
            plan = drop_family(plan, family)
    return plan


def fix_empty_entities(plan: QueryPlan, found: Findings) -> QueryPlan:
    """Rule 6: an entity with nothing to search for is dropped."""
    kept = []
    for index, entity in enumerate(plan.entities):
        try:
            essie.literal(entity.value)
        except ValueError:
            found.adjust(
                "empty_entity", f"/entities/{index}", "The entity has no letter or digit.", "dropped"
            )
            found.warn("empty_entity", "A name with no letter or digit was left out.")
        else:
            kept.append(entity)
    return plan.model_copy(update={"entities": kept})


def fix_inverted_years(plan: QueryPlan, found: Findings) -> QueryPlan:
    """Rule 7."""
    first, last = plan.filters.year_from, plan.filters.year_to
    if first is None or last is None or first <= last:
        return plan
    found.adjust(
        "year_range_inverted", "/filters/year_from", "The first year was after the last; swapped.", "replaced"
    )
    return _filters(plan, year_from=last, year_to=first)


def fix_single_compare(plan: QueryPlan, found: Findings) -> QueryPlan:
    """Rule 8: one side alone is not a comparison."""
    if _compared(plan) != 1:
        return plan
    found.adjust(
        "single_compare",
        "/entities",
        "A comparison needs two sides; the one side became a filter.",
        "replaced",
    )
    entities = [e.model_copy(update={"role": "filter"}) if e.role == "compare" else e for e in plan.entities]
    return plan.model_copy(update={"entities": entities})


def fix_too_many_compare(plan: QueryPlan, found: Findings) -> QueryPlan:
    """Rule 9: at most four sides are compared."""
    if _compared(plan) <= MAX_COMPARED:
        return plan
    seen = 0
    kept = []
    for entity in plan.entities:
        seen += entity.role == "compare"
        if entity.role == "filter" or seen <= MAX_COMPARED:
            kept.append(entity)
    found.adjust(
        "too_many_compare", "/entities", f"Only the first {MAX_COMPARED} compared names are kept.", "dropped"
    )
    found.warn("compare_truncated", f"Only the first {MAX_COMPARED} compared names were used.")
    return plan.model_copy(update={"entities": kept})


def fix_series_with_compare(plan: QueryPlan, found: Findings) -> QueryPlan:
    """Rule 10: compared groups take the one series channel."""
    if _compared(plan) < 2:
        return plan
    analysis = plan.analysis
    if isinstance(analysis, Aggregate) and analysis.series is not None:
        path, plan = "/analysis/series", _analysis(plan, series=None)
    elif isinstance(analysis, Relate) and analysis.color_by is not None:
        path, plan = "/analysis/color_by", _analysis(plan, color_by=None)
    else:
        return plan
    found.adjust("series_with_compare", path, "Compared groups already use the series channel.", "dropped")
    found.warn("series_dropped", "The split was left out because the compared names already split the chart.")
    return plan


def fix_series_not_allowed(plan: QueryPlan, found: Findings) -> QueryPlan:
    """Rule 11."""
    analysis = plan.analysis
    if isinstance(analysis, Aggregate) and analysis.series == analysis.dimension:
        found.adjust(
            "series_not_allowed", "/analysis/series", "The series repeated the dimension.", "dropped"
        )
        return _analysis(plan, series=None)
    if isinstance(analysis, Relate) and analysis.color_by in MULTI_VALUED:
        found.adjust(
            "series_not_allowed", "/analysis/color_by", "A trial has several values of this field.", "dropped"
        )
        return _analysis(plan, color_by=None)
    return plan


def fix_time_unit(plan: QueryPlan, found: Findings) -> QueryPlan:
    """Rule 12."""
    analysis = plan.analysis
    if isinstance(analysis, Aggregate) and analysis.time_unit and analysis.dimension not in DATE_DIMENSIONS:
        found.adjust(
            "time_unit_misplaced",
            "/analysis/time_unit",
            "A time unit applies to date dimensions only.",
            "dropped",
        )
        return _analysis(plan, time_unit=None)
    return plan


def fix_link(plan: QueryPlan, found: Findings) -> QueryPlan:
    """Rule 13: only drugs share an arm."""
    analysis = plan.analysis
    if (
        isinstance(analysis, Network)
        and analysis.link == "same_arm"
        and (analysis.source, analysis.target) != ("drug", "drug")
    ):
        found.adjust("link_not_applicable", "/analysis/link", "Arms link drugs only.", "replaced")
        return _analysis(plan, link="same_trial")
    return plan


def fix_limits(plan: QueryPlan, facts: Facts, found: Findings) -> QueryPlan:
    """Rules 15 and 14: a number the model wrote must be in the question, then it is clamped.

    Rule 15 runs first so that an invented 100 is not reported as a clamp before it is dropped.
    """
    analysis = plan.analysis
    if isinstance(analysis, Aggregate):
        attribute, value = "top_n", analysis.top_n
    elif isinstance(analysis, TrialList):
        attribute, value = "limit", analysis.limit
    else:
        return plan
    path = f"/analysis/{attribute}"
    if value is not None and facts.mode == "model" and not _number_is_grounded(value, facts):
        found.adjust(
            "ungrounded_number", path, f"{value} is not in the question; the default is used.", "defaulted"
        )
        return _analysis(plan, **{attribute: None})
    if value is not None and value > MAX_LIMIT:
        found.adjust("limit_clamped", path, f"{value} is above {MAX_LIMIT}.", "clamped")
        return _analysis(plan, **{attribute: MAX_LIMIT})
    return plan


def _number_is_grounded(number: int, facts: Facts) -> bool:
    from_request = facts.request is not None and facts.request.top_n == number
    return from_request or mentions_number(number, facts.question_tokens)


_COMPARABLE_CHARTS: Final[dict[str, frozenset[ChartType]]] = {
    "relate": frozenset({"scatter_plot", "table"}),
    "trial_list": frozenset({"table"}),
    "network": frozenset({"network_graph"}),
}


def fix_chart_preference(plan: QueryPlan, found: Findings) -> QueryPlan:
    """Rule 16: a preference is kept only inside what the data shape allows."""
    preference = plan.chart_preference
    if preference is None or preference in _allowed_charts(plan):
        return plan
    found.adjust(
        "chart_preference_incompatible",
        "/chart_preference",
        f"{preference} does not fit this result.",
        "dropped",
    )
    found.warn(
        "chart_preference_ignored", f"The {preference} chart does not fit this question, so it was not used."
    )
    return plan.model_copy(update={"chart_preference": None})


def _allowed_charts(plan: QueryPlan) -> frozenset[ChartType]:
    analysis = plan.analysis
    if isinstance(analysis, Aggregate):
        if analysis.dimension in DATE_DIMENSIONS:
            return frozenset({"time_series", "bar_chart", "table"})
        if analysis.dimension == "enrollment":
            return frozenset({"histogram", "bar_chart", "table"})
        return frozenset({"bar_chart", "table"})
    if analysis.kind == "total":
        return frozenset({"bar_chart", "table"} if _compared(plan) >= 2 else {"metric", "table"})
    return _COMPARABLE_CHARTS.get(analysis.kind, frozenset(get_args(ChartType)))


def fix_ungrounded_phases(plan: QueryPlan, facts: Facts, found: Findings) -> QueryPlan:
    """Rule 17: a phase the evidence phrase does not state is dropped.

    Without any phrase there is nothing to compare with; rule 20 then sends the plan back to the model.
    """
    phrases = [item.phrase for item in plan.filters.evidence if item.family == "phases"]
    if "phases" in facts.request_families or not phrases:
        return plan
    grounded = [phase for phase in plan.filters.phases if any(phase_is_grounded(phase, p) for p in phrases)]
    for phase in plan.filters.phases:
        if phase not in grounded:
            found.adjust(
                "ungrounded_filter_value",
                "/filters/phases",
                f"{phase} is not stated by its evidence.",
                "dropped",
            )
    if len(grounded) == len(plan.filters.phases):
        return plan
    return drop_family(plan, "phases", grounded) if not grounded else _filters(plan, phases=grounded)


# --- the blocking rules: 18 to 23 -----------------------------------------------------------------


def find_blocking(plan: QueryPlan, facts: Facts, found: Findings) -> None:
    """Record the problems that code cannot repair and the model can; they go to the repair turn."""
    if facts.mode == "model":
        _ungrounded_entities(plan, facts, found)
        _ungrounded_years(plan, facts, found)
        _ungrounded_filters(plan, facts, found)
    _mixed_compare_kinds(plan, found)
    if facts.mode != "structured":
        _relate_same_measure(plan, found)
        _network_pair_unsupported(plan, found)


def _ungrounded_entities(plan: QueryPlan, facts: Facts, found: Findings) -> None:
    for index, entity in enumerate(plan.entities):
        if not is_grounded(entity.value, facts.known):
            found.block(
                "ungrounded_entity",
                f"/entities/{index}/value",
                f"'{entity.value}' is not in the question or the fields. Copy the words, or drop the entity.",
            )


def _ungrounded_years(plan: QueryPlan, facts: Facts, found: Findings) -> None:
    relative = has_relative_time(facts.question)
    for attribute in ("year_from", "year_to"):
        year: int | None = getattr(plan.filters, attribute)
        if year is None or str(year) in facts.known:
            continue
        in_reach = facts.today.year - _YEARS_BEFORE_TODAY <= year <= facts.today.year + _YEARS_AFTER_TODAY
        if relative and in_reach:
            found.adjust(
                "relative_year",
                f"/filters/{attribute}",
                f"{year} was read from a phrase relative to today.",
                "kept",
            )
        else:
            found.block(
                "ungrounded_year",
                f"/filters/{attribute}",
                f"{year} is not written in the question; use null unless it is.",
            )


def _ungrounded_filters(plan: QueryPlan, facts: Facts, found: Findings) -> None:
    for family in FAMILY_ENUMS:
        if not getattr(plan.filters, family) or family in facts.request_families:
            continue
        phrases = [item.phrase for item in plan.filters.evidence if item.family == family]
        if not any(_phrase_in_question(phrase, facts) for phrase in phrases):
            found.block(
                "ungrounded_filter",
                f"/filters/{family}",
                f"No phrase of the question is quoted for {family}. Quote it, or leave the list empty.",
            )


def _phrase_in_question(phrase: str, facts: Facts) -> bool:
    words = tokens(phrase)
    return bool(words) and all(word in facts.question_tokens for word in words)


def _mixed_compare_kinds(plan: QueryPlan, found: Findings) -> None:
    if len({entity.kind for entity in plan.entities if entity.role == "compare"}) > 1:
        found.block("mixed_compare_kinds", "/entities", "Compared names must all be of one kind.")


def _relate_same_measure(plan: QueryPlan, found: Findings) -> None:
    if isinstance(plan.analysis, Relate) and plan.analysis.x == plan.analysis.y:
        found.block("relate_same_measure", "/analysis/y", "x and y must be different measures.")


def _network_pair_unsupported(plan: QueryPlan, found: Findings) -> None:
    analysis = plan.analysis
    if not isinstance(analysis, Network):
        return
    if analysis.source == analysis.target and analysis.source in SINGLE_VALUED_NODES:
        found.block(
            "network_pair_unsupported", "/analysis", f"A trial has one {analysis.source}, so none links two."
        )
    elif _compared(plan):
        found.block(
            "network_pair_unsupported", "/analysis", "A network cannot be combined with compared names."
        )
