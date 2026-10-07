"""The trials two compared groups share: why their bars can add up to more than the whole.

Only a comparison of exactly two groups has an overlap here: it is one count call, the search that selects
what both groups select. For three to five groups no overlap is computed, and the response says so, because
the number of trials in at least two of them cannot be had from counts of intersections of two.
"""

from collections.abc import Mapping
from dataclasses import dataclass

import structlog
from structlog.typing import FilteringBoundLogger

from ctviz.contract.response import Note
from ctviz.ctgov import essie
from ctviz.ctgov.client import RequestLog
from ctviz.ctgov.params import QUERY_PARAMETERS, Params
from ctviz.engine.lower import EnginePlan
from ctviz.engine.registry import Registry
from ctviz.errors import AppError, DeadlineExceeded

_log: FilteringBoundLogger = structlog.get_logger()


@dataclass(frozen=True)
class Overlap:
    """The trials in both groups of a two-group comparison, or None with the reason there is no number."""

    shared: int | None
    warnings: tuple[Note, ...] = ()


async def shared_trials(
    plan: EnginePlan, matched: Mapping[str, int], client: Registry, ctx: RequestLog
) -> Overlap:
    """The trials in both groups of a two-group comparison, by one count call; None for anything else.

    The count is an extra: when the registry cannot answer it, the chart is still returned, without the
    overlap and with a warning.
    """
    if len(plan.scopes) != 2 or not all(matched[scope.id] for scope in plan.scopes):
        return Overlap(None)
    both = _both(plan.scopes[0].params(), plan.scopes[1].params())
    if both is None:
        return Overlap(0)  # the groups ask for statuses that no trial can have together
    try:
        return Overlap(await client.count(both, ctx, origin="execution"))
    except DeadlineExceeded:
        raise
    except AppError as failure:
        _log.warning("overlap_count_failed", error=failure.message)
        return Overlap(
            None,
            (
                Note(
                    code="overlap_not_counted",
                    message="The number of trials that belong to both groups could not be counted, so it "
                    "is not shown; the groups may share trials.",
                ),
            ),
        )


def _both(first: Params, second: Params) -> Params | None:
    """One search that selects what both do, or None when the two cannot select a common trial.

    Two searches on one `query.*` parameter become one `(a) AND (b)`, which the registry reads as both
    terms. Any other parameter is a list of values to match (`filter.overallStatus` is `A|B`): the registry
    takes one list and rejects an `AND` of two, so the two lists are intersected.
    """
    texts: dict[str, list[str]] = {}
    lists: dict[str, list[str]] = {}
    for name, value in (*first.texts, *second.texts):
        if name in QUERY_PARAMETERS:
            texts.setdefault(name, [])
            if value not in texts[name]:
                texts[name].append(value)
        elif name in lists:
            lists[name] = [token for token in lists[name] if token in value.split("|")]
        else:
            lists[name] = value.split("|")
    if any(not tokens for tokens in lists.values()):
        return None
    joined = tuple(
        (name, " AND ".join(f"({value})" for value in values) if len(values) > 1 else values[0])
        for name, values in texts.items()
    )
    shared = tuple((name, "|".join(tokens)) for name, tokens in lists.items())
    advanced = list(dict.fromkeys(expr for expr in (first.advanced, second.advanced) if expr is not None))
    return Params((*joined, *shared), essie.and_(*advanced) if advanced else None)
