"""The trials two compared groups share: why their bars can add up to more than the whole."""

from collections import defaultdict
from collections.abc import Mapping

from ctviz.ctgov import essie
from ctviz.ctgov.client import RequestLog
from ctviz.ctgov.params import Params
from ctviz.engine.lower import EnginePlan
from ctviz.engine.registry import Registry


async def shared_trials(
    plan: EnginePlan, matched: Mapping[str, int], client: Registry, ctx: RequestLog
) -> int | None:
    """The trials in both groups of a two-group comparison, by one count call; None for anything else."""
    if len(plan.scopes) != 2 or not all(matched[scope.id] for scope in plan.scopes):
        return None
    both = _both(plan.scopes[0].params(), plan.scopes[1].params())
    return await client.count(both, ctx, origin="execution")


def _both(first: Params, second: Params) -> Params:
    """One search that selects what both do: two searches on one parameter become one `(a) AND (b)`."""
    texts: defaultdict[str, list[str]] = defaultdict(list)
    for name, value in (*first.texts, *second.texts):
        texts[name].append(value)
    joined = tuple((name, " AND ".join(f"({value})" for value in values)) for name, values in texts.items())
    advanced = [expr for expr in (first.advanced, second.advanced) if expr is not None]
    return Params(joined, essie.and_(*advanced) if advanced else None)
