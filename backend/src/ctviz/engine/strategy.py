"""Choosing how to fetch the trials: a pure function of the plan and the size of each scope.

The rows of the table in section 4.9 are applied in order, and the first that matches wins. Every scope of
one question is fetched the same way, so that compared groups are counted alike. Sample-then-recount (a
walk for candidates, then exact counts of the leaders) is not implemented: a plan that would use it takes
the capped walk instead, which says plainly that it read a recent subset.
"""

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from ctviz.catalog.fields import Window
from ctviz.contract.response import Clarification, Note, Outcome, StrategyName
from ctviz.ctgov import essie
from ctviz.ctgov.essie import Expr
from ctviz.ctgov.params import Scope
from ctviz.engine import windows
from ctviz.engine.evidence import SORT_PIECES, projection
from ctviz.engine.fanout import fan_out_bill
from ctviz.engine.lower import EnginePlan, ListRows
from ctviz.settings import Settings

MAX_WALK_PERIODS: Final = 60
MIN_FAN_OUT_PERIODS: Final = 5
_NEWEST_FIRST: Final = "StudyFirstPostDate:desc"
# A drug and drug network reads only trials that list at least two interventions when it is capped.
_AT_LEAST_TWO_INTERVENTIONS: Final = essie.range_("Intervention:size", 2, None)


@dataclass(frozen=True)
class Limits:
    """The caps of section 4.9."""

    one_page_max: int
    walk_cap: int
    max_fanout_requests: int

    @classmethod
    def from_settings(cls, settings: Settings) -> "Limits":
        return cls(settings.one_page_max, settings.walk_cap, settings.max_fanout_requests)


@dataclass(frozen=True)
class ScopeRun:
    """How one scope is fetched."""

    scope: Scope
    matched: int  # the probe's count
    strategy: StrategyName
    reason: str  # said in `meta.interpretation.strategy`
    fields: tuple[str, ...] = ()  # the projection of a walk or a sorted page
    limit: int = 0  # trials a walk reads, rows a sorted page returns
    sort: str | None = None
    # The scope with the presence push-down added, for a walk over a field that not every trial has.
    walk_scope: Scope | None = None


@dataclass(frozen=True)
class ExecutionPlan:
    runs: tuple[ScopeRun, ...]  # in the order of the plan's scopes
    window: Window | None  # the periods of a date axis, shortened when the plan asked for too many
    warnings: tuple[Note, ...]


@dataclass(frozen=True)
class _Choice:
    strategy: StrategyName
    reason: str
    limit: int = 0
    window: Window | None = None


def choose_strategy(
    plan: EnginePlan, matched: Mapping[str, int], limits: Limits, *, prefer_walk: bool
) -> ExecutionPlan | Outcome:
    """The strategy of every scope, or an outcome when nothing can be drawn or the question is too broad."""
    if not any(matched[scope.id] for scope in plan.scopes):
        return Outcome(kind="no_data", reason="no_trials_matched", message="No trials match this question.")
    window, warnings = _window(plan.window)
    choice = _choose(plan, max(matched[scope.id] for scope in plan.scopes), window, limits, prefer_walk)
    if isinstance(choice, Outcome):
        return choice
    if choice.window != window and choice.window is not None:
        warnings.append(_clamped(plan, choice.window))
    runs = []
    for scope in plan.scopes:
        count = matched[scope.id]
        if count == 0:
            runs.append(ScopeRun(scope, 0, "none", "The scope matches no trials."))
            warnings.append(
                Note(
                    code="series_matched_nothing",
                    message=f"No trials match '{scope.label}', so it is shown as an empty series.",
                )
            )
        else:
            runs.append(_run(plan, scope, count, choice, limits))
    return ExecutionPlan(tuple(runs), choice.window or window, tuple(warnings))


def _choose(
    plan: EnginePlan, biggest: int, window: Window | None, limits: Limits, prefer_walk: bool
) -> _Choice | Outcome:
    if isinstance(plan.rows, ListRows):
        return _Choice("sorted_page", "A list of trials is one page in the requested order.", plan.top_n)
    if not plan.dimensions and plan.rows is None:
        return _Choice("count_fan_out", "A single count, with a few trials to cite.", window=window)

    can_fan_out = plan.rows is None and plan.relation != "network"
    bill = fan_out_bill(plan, window) if can_fan_out else None
    fits = bill is not None and bill <= limits.max_fanout_requests
    fan_out = _Choice(
        "count_fan_out",
        f"{biggest:,} trials is more than one page; the registry counted each group with {bill} requests.",
        window=window,
    )
    walk = _Choice("walk", f"All {biggest:,} trials were read and grouped here.", limits.walk_cap)
    if biggest <= limits.one_page_max:
        return dataclasses.replace(walk, limit=limits.one_page_max, window=window)
    if fits and not prefer_walk:
        return fan_out
    if biggest <= limits.walk_cap:
        return dataclasses.replace(walk, window=window)
    if fits:
        return fan_out
    if plan.dimensions and plan.dimensions[0].spec.kind == "date":
        return _shortened(plan, biggest, window, limits)
    return _Choice(
        "capped_walk",
        f"More than {limits.walk_cap:,} trials match, so the {limits.walk_cap:,} most recently "
        "first-posted were read.",
        limits.walk_cap,
        window,
    )


def _shortened(plan: EnginePlan, biggest: int, window: Window | None, limits: Limits) -> _Choice | Outcome:
    """Row 7: a time axis cannot come from a recent subset, so count the latest periods that fit the bill."""
    if window is not None and plan.rows is None and plan.relation != "network":
        for count in range(windows.length(window), MIN_FAN_OUT_PERIODS - 1, -1):
            candidate = windows.latest(window, count)
            bill = fan_out_bill(plan, candidate)
            if bill is not None and bill <= limits.max_fanout_requests:
                reason = f"{biggest:,} trials is too many to read; the latest {count} periods were counted."
                return _Choice("count_fan_out", reason, window=candidate)
    return Outcome(
        kind="clarification",
        reason="too_broad",
        message=f"{biggest:,} trials match, too many to show by period. "
        "Narrow the question to a drug, a condition, a sponsor or a country.",
        clarification=Clarification(reason="too_broad", missing_fields=[], options=[]),
    )


def _window(window: Window | None) -> tuple[Window | None, list[Note]]:
    """The plan's window, cut to the most periods a walk honours."""
    if window is None or windows.length(window) <= MAX_WALK_PERIODS:
        return window, []
    shortened = windows.latest(window, MAX_WALK_PERIODS)
    return shortened, [
        Note(
            code="window_clamped",
            message=f"The time axis was shortened to its latest {MAX_WALK_PERIODS} periods "
            f"({shortened.first} to {shortened.last}).",
        )
    ]


def _clamped(plan: EnginePlan, window: Window) -> Note:
    first = plan.window.first if plan.window is not None else window.first
    return Note(
        code="window_clamped",
        message=f"Too many periods to count one by one: the axis shows {window.first} to {window.last} "
        f"instead of {first} to {window.last}.",
    )


def _run(plan: EnginePlan, scope: Scope, matched: int, choice: _Choice, limits: Limits) -> ScopeRun:
    if choice.strategy == "sorted_page" and isinstance(plan.rows, ListRows):
        sort = f"{SORT_PIECES[plan.rows.sort_by]}:{plan.rows.order}"
        return ScopeRun(
            scope, matched, choice.strategy, choice.reason, projection(plan, scope), choice.limit, sort
        )
    if choice.strategy == "count_fan_out":
        return ScopeRun(scope, matched, choice.strategy, choice.reason)
    is_capped = choice.strategy == "capped_walk"
    exprs = _presence(plan, is_capped) if is_capped or matched > limits.one_page_max else ()
    return ScopeRun(
        scope,
        matched,
        choice.strategy,
        choice.reason,
        projection(plan, scope),
        choice.limit,
        _NEWEST_FIRST if is_capped else None,
        walk_scope=dataclasses.replace(scope, extra=(*scope.extra, *exprs)) if exprs else None,
    )


def _presence(plan: EnginePlan, is_capped: bool) -> tuple[Expr, ...]:
    """Restrict a large walk to the trials that have the fields it groups by."""
    exprs = [dimension.spec.presence for dimension in plan.dimensions if dimension.spec.presence is not None]
    keys = {dimension.spec.key for dimension in plan.dimensions}
    if is_capped and plan.relation == "network" and keys == {"drug"}:
        exprs.append(_AT_LEAST_TWO_INTERVENTIONS)
    return tuple(dict.fromkeys(exprs))
