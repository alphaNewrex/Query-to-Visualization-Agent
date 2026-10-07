"""Choosing how to fetch the trials: a pure function of the plan and the size of each scope.

The rows of the table in section 4.9 are applied in order, and the first that matches wins. Every scope of
one question is fetched the same way, so that compared groups are counted alike. Sample-then-recount (a
walk for candidates, then exact counts of the leaders) is not implemented.

A walk reads every trial of a scope; there is no cap on the number. What bounds it is time: a walk whose
estimated duration does not fit what is left of the request's deadline is not started. Nothing is read from
a recent subset in its place. The question is answered by exact counts when the groups are countable, and
otherwise with a `too_broad` clarification that states how many trials match.
"""

import dataclasses
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from ctviz.catalog.fields import Window
from ctviz.contract.response import Clarification, Note, Outcome, StrategyName
from ctviz.ctgov.essie import Expr
from ctviz.ctgov.params import Scope
from ctviz.ctgov.partition import SEQUENTIAL_PAGES
from ctviz.engine import windows
from ctviz.engine.evidence import SORT_PIECES, projection
from ctviz.engine.fanout import fan_out_bill
from ctviz.engine.lower import EnginePlan, ListRows
from ctviz.settings import Settings

MAX_WALK_PERIODS: Final = 60
MIN_FAN_OUT_PERIODS: Final = 5
# A split walk makes about one count request for every page it reads, on top of the pages.
_COUNTS_PER_PAGE: Final = 1.0


@dataclass(frozen=True)
class Limits:
    """The limits of section 4.9: the sizes of a request and of a fan-out, and the time a walk may take."""

    one_page_max: int
    max_fanout_requests: int
    walk_pages_per_s: float = 5.0  # registry requests a walk makes per second, measured with split walks
    walk_budget_s: float = math.inf  # seconds left for reading trials; unbounded when there is no deadline

    @classmethod
    def from_settings(cls, settings: Settings, walk_budget_s: float = math.inf) -> "Limits":
        return cls(
            settings.one_page_max, settings.max_fanout_requests, settings.walk_pages_per_s, walk_budget_s
        )

    def walk_seconds(self, scopes: Iterable[int]) -> float:
        """How long reading scopes of these sizes takes: every page, and the counts that split a long walk."""
        requests = 0.0
        for trials in scopes:
            pages = math.ceil(trials / self.one_page_max)
            requests += pages * (1 + (_COUNTS_PER_PAGE if pages > SEQUENTIAL_PAGES else 0))
        return requests / self.walk_pages_per_s


@dataclass(frozen=True)
class ScopeRun:
    """How one scope is fetched."""

    scope: Scope
    matched: int  # the probe's count
    strategy: StrategyName
    reason: str  # said in `meta.interpretation.strategy`
    fields: tuple[str, ...] = ()  # the projection of a walk or a sorted page
    limit: int = 0  # rows a sorted page returns
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
    counts = [matched[scope.id] for scope in plan.scopes]
    choice = _choose(plan, counts, window, limits, prefer_walk)
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
    plan: EnginePlan, counts: Sequence[int], window: Window | None, limits: Limits, prefer_walk: bool
) -> _Choice | Outcome:
    biggest = max(counts)
    if isinstance(plan.rows, ListRows):
        return _Choice("sorted_page", "A list of trials is one page in the requested order.", plan.top_n)
    if not plan.dimensions and plan.rows is None and plan.measure is None:
        return _Choice("count_fan_out", "A single count, with a few trials to cite.", window=window)

    can_fan_out = plan.rows is None and plan.relation != "network" and plan.measure is None
    bill = fan_out_bill(plan, window) if can_fan_out else None
    fits = bill is not None and bill <= limits.max_fanout_requests
    fan_out = _Choice(
        "count_fan_out",
        f"{biggest:,} trials is more than one page; the registry counted each group with {bill} requests.",
        window=window,
    )
    walk = _Choice("walk", f"All {biggest:,} trials were read and grouped here.", window=window)
    if biggest <= limits.one_page_max:
        return walk
    if fits and not prefer_walk:
        return fan_out
    seconds = limits.walk_seconds(counts)
    if seconds <= limits.walk_budget_s:
        return walk
    if fits:
        return fan_out
    if plan.dimensions and plan.dimensions[0].spec.kind == "date":
        return _shortened(plan, counts, seconds, window, limits)
    return _too_broad(counts, seconds, limits)


def _too_broad(counts: Sequence[int], seconds: float, limits: Limits) -> Outcome:
    """A scope too large to read in the time left: say how large, and what would make it smaller."""
    size = f"{max(counts):,} trials match" + (" the largest group" if len(counts) > 1 else "")
    spent = f"reading all {sum(counts):,} would take about {math.ceil(seconds)} s"
    left = (
        ""
        if math.isinf(limits.walk_budget_s)
        else f", and about {max(0, int(limits.walk_budget_s))} s are left"
    )
    return Outcome(
        kind="clarification",
        reason="too_broad",
        message=f"{size}, and {spent}{left}. Nothing was read, because a partial read would not be "
        "a true answer. Narrow the question to a drug, a condition, a sponsor, a country or a date range, "
        "or group by a closed list such as phase or status, which is counted exactly at any size.",
        clarification=Clarification(reason="too_broad", missing_fields=[], options=[]),
    )


def _shortened(
    plan: EnginePlan, counts: Sequence[int], seconds: float, window: Window | None, limits: Limits
) -> _Choice | Outcome:
    """A time axis cannot come from a part of the trials, so count the latest periods that fit the bill."""
    if window is not None and plan.rows is None and plan.relation != "network":
        for count in range(windows.length(window), MIN_FAN_OUT_PERIODS - 1, -1):
            candidate = windows.latest(window, count)
            bill = fan_out_bill(plan, candidate)
            if bill is not None and bill <= limits.max_fanout_requests:
                reason = (
                    f"{max(counts):,} trials is too many to read; the latest {count} periods were counted."
                )
                return _Choice("count_fan_out", reason, window=candidate)
    return _too_broad(counts, seconds, limits)


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
    exprs = _presence(plan) if matched > limits.one_page_max else ()
    return ScopeRun(
        scope,
        matched,
        choice.strategy,
        choice.reason,
        projection(plan, scope),
        choice.limit,
        walk_scope=dataclasses.replace(scope, extra=(*scope.extra, *exprs)) if exprs else None,
    )


def _presence(plan: EnginePlan) -> tuple[Expr, ...]:
    """Restrict a large walk to the trials that have the fields it groups by."""
    exprs = [dimension.spec.presence for dimension in plan.dimensions if dimension.spec.presence is not None]
    return tuple(dict.fromkeys(exprs))
