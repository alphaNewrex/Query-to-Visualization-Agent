"""What the shaper hands to the builders: cut and ordered frames, and the per-trial rows.

`ShapedResult` is the plan's name for the output of `engine/shape.py`; its parts are declared here
because the builders are its only readers. A frame holds one `Cell` per drawn mark of a category or
entity axis, in display order with "Other (k more)" last, so a builder does no cutting or ordering of
its own. What the plan fixes the builders rebuild themselves, filling missing cells with zero: the
periods of a date axis, the bins of enrollment, and the grid of an axis with a series.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from ctviz.catalog.fields import Evidence
from ctviz.contract.plan import NodeKind
from ctviz.contract.response import Note, Scalar, TruncationItem
from ctviz.engine.frame import Cell, Frame


def node_id(kind: NodeKind, key: str) -> str:
    """The id of a network node: the kind keeps a drug and a sponsor of the same name apart."""
    return f"{kind}:{key}"


@dataclass(frozen=True)
class TrialRow:
    """One trial of a table or a scatter plot: the values drawn and where each was read."""

    nct_id: str
    title: str
    values: Mapping[str, Scalar]  # row keys of section 5.3: enrollment, phase, start_date, lead_sponsor, ...
    evidence: tuple[Evidence, ...]
    group: str | None = None  # the colour group of a scatter plot


@dataclass(frozen=True)
class NetworkNode:
    kind: NodeKind
    label: str
    cell: Cell  # key[0] is the entity key; trials is its count over all analysed trials

    @property
    def id(self) -> str:
        return node_id(self.kind, self.cell.key[0])


@dataclass(frozen=True)
class NetworkEdge:
    source: NetworkNode
    target: NetworkNode
    cell: Cell  # the trials in which both occur

    @property
    def id(self) -> str:
        return f"{self.source.id}|{self.target.id}"


@dataclass(frozen=True)
class ShapedResult:
    """The answer to one plan, ready to be drawn: one frame per scope plus whatever the type needs."""

    frames: tuple[Frame, ...]
    truncation: tuple[TruncationItem, ...] = ()
    trials: tuple[TrialRow, ...] = ()  # table and scatter plot
    nodes: tuple[NetworkNode, ...] = ()
    edges: tuple[NetworkEdge, ...] = ()
    trials_in_several_series: int | None = None  # trials in both groups of a two-group comparison
    warnings: tuple[Note, ...] = ()  # caveats only the shaper knows, such as anchor_omitted
