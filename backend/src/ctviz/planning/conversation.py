"""What a follow-up kept and changed: a diff of the previous plan and the new one, written by code.

The model returns a complete plan and never says what it carried over. The two lists of
`meta.conversation` are derived here from the plans, so they describe what the answer really uses.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from ctviz.contract.plan import FAMILY_FIELDS, Aggregate, Entity, FilterFamily, QueryPlan
from ctviz.contract.response import Conversation
from ctviz.planning.grounding import tokens

_FAMILY_LABELS: dict[FilterFamily, str] = {
    "phases": "phase",
    "statuses": "status",
    "exclude_statuses": "excluded status",
    "study_types": "study type",
    "sponsor_classes": "sponsor class",
    "intervention_types": "intervention type",
    "sexes": "sex",
    "age_groups": "age group",
    "allocations": "allocation",
    "maskings": "masking",
    "primary_purposes": "primary purpose",
    "has_results": "results posted",
    "intervention_models": "intervention model",
}
assert set(_FAMILY_LABELS) >= set(FAMILY_FIELDS)
_ROLE_PREFIX = {"filter": "", "compare": "compared ", "exclude": "excluded "}


@dataclass(frozen=True)
class Described:
    """The conversation block, and the carried items that are scope (what the trials are), not the chart."""

    conversation: Conversation
    scope: tuple[str, ...]


NEW_QUESTION = Conversation(is_follow_up=False, carried_over=[], changed=[])


def describe(previous: QueryPlan | None, plan: QueryPlan) -> Described:
    """Diff two plans; with no previous plan, or when nothing of it was kept, the lists are empty."""
    if previous is None:
        return Described(NEW_QUESTION, ())
    carried: list[str] = []
    changed: list[str] = []
    scope: list[str] = []
    _entities(previous, plan, scope, changed)
    _filters(previous, plan, scope, changed)
    _years(previous, plan, scope, changed)
    carried.extend(scope)
    _analysis(previous, plan, carried, changed)
    if previous.chart_preference == plan.chart_preference:
        if plan.chart_preference is not None:
            carried.append(f"chart: {_words(plan.chart_preference)}")
    elif plan.chart_preference is not None:
        changed.append(f"chart: {_words(plan.chart_preference)}")
    else:
        changed.append(f"removed the {_words(str(previous.chart_preference))} chart preference")
    is_follow_up = bool(carried) or previous.analysis.kind == "clarify"
    if not is_follow_up:
        return Described(NEW_QUESTION, ())  # nothing was kept: a new question, not an edit of the old one
    return Described(
        Conversation(is_follow_up=is_follow_up, carried_over=carried, changed=changed), tuple(scope)
    )


def _words(code: str) -> str:
    return code.replace("PHASE", "phase ").replace("_", " ").lower().strip()


def _list(values: Iterable[str]) -> str:
    return ", ".join(_words(value) for value in values)


def _label(entity: Entity) -> str:
    return f"{_ROLE_PREFIX[entity.role]}{entity.kind}: {entity.value}"


def _entity_key(entity: Entity) -> tuple[str, str, tuple[str, ...]]:
    return entity.kind, entity.role, tokens(entity.value)


def _entities(previous: QueryPlan, plan: QueryPlan, carried: list[str], changed: list[str]) -> None:
    before = {_entity_key(entity): entity for entity in previous.entities}
    after = {_entity_key(entity): entity for entity in plan.entities}
    carried.extend(_label(entity) for key, entity in after.items() if key in before)
    removed = [entity for key, entity in before.items() if key not in after]
    added = [entity for key, entity in after.items() if key not in before]
    for entity in list(added):
        same_slot = [old for old in removed if (old.kind, old.role) == (entity.kind, entity.role)]
        if len(same_slot) == 1 and sum((e.kind, e.role) == (entity.kind, entity.role) for e in added) == 1:
            prefix = _ROLE_PREFIX[entity.role]
            changed.append(f"{prefix}{entity.kind}: {entity.value} replaces {same_slot[0].value}")
            removed.remove(same_slot[0])
            added.remove(entity)
    changed.extend(f"added {_label(entity)}" for entity in added)
    changed.extend(f"removed {_label(entity)}" for entity in removed)


def _filters(previous: QueryPlan, plan: QueryPlan, carried: list[str], changed: list[str]) -> None:
    for family, label in _FAMILY_LABELS.items():
        old, new = list(getattr(previous.filters, family)), list(getattr(plan.filters, family))
        if set(old) == set(new):
            if new:
                carried.append(f"{label}: {_list(new)}")
        elif not old:
            changed.append(f"added {label}: {_list(new)}")
        elif not new:
            changed.append(f"removed {label} filter ({_list(old)})")
        else:
            changed.append(f"{label}: {_list(new)} (was {_list(old)})")


def _years(previous: QueryPlan, plan: QueryPlan, carried: list[str], changed: list[str]) -> None:
    for attribute, word in (("year_from", "from"), ("year_to", "to")):
        old, new = getattr(previous.filters, attribute), getattr(plan.filters, attribute)
        if old == new:
            if new is not None:
                carried.append(f"{word} {new}")
        elif old is None:
            changed.append(f"added {word} {new}")
        elif new is None:
            changed.append(f"removed {word} {old}")
        else:
            changed.append(f"{word} {new} (was {old})")


def _analysis(previous: QueryPlan, plan: QueryPlan, carried: list[str], changed: list[str]) -> None:
    old, new = previous.analysis, plan.analysis
    if old.kind in ("clarify", "unsupported") or new.kind in ("clarify", "unsupported"):
        return
    if not (isinstance(old, Aggregate) and isinstance(new, Aggregate)):
        if old.kind == new.kind:
            carried.append(f"analysis: {new.kind.replace('_', ' ')}")
        else:
            changed.append(f"analysis: {new.kind.replace('_', ' ')} (was {old.kind.replace('_', ' ')})")
        return
    if old.dimension == new.dimension:
        carried.append(f"grouped by {_words(new.dimension)}")
    else:
        changed.append(f"grouped by {_words(new.dimension)} (was {_words(old.dimension)})")
    if old.series == new.series:
        if new.series is not None:
            carried.append(f"split by {_words(new.series)}")
    elif old.series is None:
        changed.append(f"split by {_words(str(new.series))}")
    elif new.series is None:
        changed.append(f"removed the split by {_words(old.series)}")
    else:
        changed.append(f"split by {_words(new.series)} (was {_words(old.series)})")
    if old.time_unit != new.time_unit and new.time_unit is not None:
        changed.append(f"time unit: {new.time_unit}")
    if old.top_n != new.top_n and new.top_n is not None:
        changed.append(f"top {new.top_n}")
    if (old.statistic, old.of) != (new.statistic, new.of):
        measure = "count" if new.statistic is None else f"{new.statistic} of {_words(str(new.of))}"
        changed.append(f"measure: {measure}")
