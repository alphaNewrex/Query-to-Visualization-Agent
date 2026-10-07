"""Network pruning: the strongest nodes and links of a co-occurrence frame, by a fixed and stated rule.

Keep the top nodes by distinct trials, drop links below the minimum weight, keep the heaviest links, and
drop nodes left without one. Every tie is broken by key, so the same data always gives the same graph.
"""

from dataclasses import dataclass
from typing import Final

from ctviz.contract.response import Note, TruncationItem
from ctviz.engine.frame import Cell, Frame
from ctviz.engine.lower import EnginePlan

NODES_PER_SIDE: Final = 15
NODES_OF_ONE_KIND: Final = 30
MIN_LINKS: Final = 3
# One node in more than this share of the analysed trials turns the graph into a star around it.
ANCHOR_SHARE: Final = 0.6


@dataclass(frozen=True)
class PrunedNetwork:
    """The links and nodes that survive, and what was said about getting there."""

    links: tuple[Cell, ...]
    nodes: tuple[tuple[Cell, ...], ...]  # one tuple per side; one side only when both ends are one kind
    truncation: tuple[TruncationItem, ...]
    warnings: tuple[Note, ...]
    assumptions: tuple[str, ...]
    is_sparse: bool = False  # fewer than MIN_LINKS links even at a minimum weight of one


def prune_network(frame: Frame, plan: EnginePlan) -> PrunedNetwork:
    one_kind = plan.dimensions[0].spec.key == plan.dimensions[1].spec.key
    warnings: list[Note] = []
    assumptions: list[str] = []
    all_links = list(frame.cells.values())
    sides = [dict(table) for table in frame.marginals[: 1 if one_kind else 2]]

    anchor = _anchor(frame, plan) if one_kind else None
    if anchor is not None:
        label = sides[0][anchor].labels[0]
        sides[0].pop(anchor)
        all_links = [cell for cell in all_links if anchor not in cell.key]
        sentence = (
            f"'{label}' is in most of the analysed trials, so it is left out and the graph shows how the "
            "other drugs go with each other."
        )
        assumptions.append(sentence)
        warnings.append(Note(code="anchor_omitted", message=sentence))

    per_side = NODES_OF_ONE_KIND if one_kind else NODES_PER_SIDE
    kept = [_top(side, per_side) for side in sides]
    kept_keys = [set(side) for side in kept]
    end_keys = kept_keys * 2 if one_kind else kept_keys  # a link of one kind of node has both ends in one set
    candidates = [
        cell for cell in all_links if all(key in keys for key, keys in zip(cell.key, end_keys, strict=True))
    ]
    links = _heaviest(candidates, plan.min_link_weight, plan.max_links)
    if len(links) < MIN_LINKS and plan.min_link_weight > 1:
        links = _heaviest(candidates, 1, plan.max_links)
        assumptions.append(
            "Links that join two nodes in a single trial are shown because few trials share more."
        )
    is_sparse = len(links) < MIN_LINKS

    ends = [{cell.key[position] for cell in links} for position in range(2)]
    nodes = tuple(
        tuple(
            node
            for node in side.values()
            if node.key[0] in (ends[0] | ends[1] if one_kind else ends[position])
        )
        for position, side in enumerate(kept)
    )
    node_total = sum(len(side) for side in sides)
    node_shown = sum(len(side) for side in nodes)
    truncation = (
        TruncationItem(
            scope="nodes",
            shown=node_shown,
            total=node_total,
            rule=f"The {per_side} largest per side, by trials, that have a link.",
        ),
        TruncationItem(
            scope="edges",
            shown=len(links),
            total=len(all_links),
            rule=f"Links of at least {plan.min_link_weight} trials, the {plan.max_links} heaviest.",
        ),
    )
    return PrunedNetwork(links, nodes, truncation, tuple(warnings), tuple(assumptions), is_sparse)


def _anchor(frame: Frame, plan: EnginePlan) -> str | None:
    """The node that most trials of a scope naming a drug contain, when the graph is of drugs.

    When several nodes pass the threshold, the one the scope names wins, then the largest.
    """
    named = {term.text.casefold() for term in frame.scope.terms if term.kind == "drug"}
    if plan.dimensions[0].spec.key != "drug" or not named or not frame.analyzed:
        return None
    common = [node for node in frame.marginals[0].values() if node.trials / frame.analyzed > ANCHOR_SHARE]
    pool = [node for node in common if node.labels[0].casefold() in named] or common
    return min(pool, key=lambda node: (-node.trials, node.key)).key[0] if pool else None


def _top(nodes: dict[str, Cell], count: int) -> dict[str, Cell]:
    ranked = sorted(nodes.values(), key=lambda node: (-node.trials, node.key))
    return {node.key[0]: node for node in ranked[:count]}


def _heaviest(cells: list[Cell], minimum: int, count: int) -> tuple[Cell, ...]:
    heavy = [cell for cell in cells if cell.trials >= minimum]
    return tuple(sorted(heavy, key=lambda cell: (-cell.trials, cell.key))[:count])
