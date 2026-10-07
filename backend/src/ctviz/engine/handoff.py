"""The hand-over to the builders: what the shaper kept, laid out the way `viz.shaped` reads it.

The shaper works on cells in display order. The builders want one `Frame` per scope whose cells are the
drawn marks, the enrollment bins, the trials of a table or scatter plot, and a network as nodes and
links. Nothing is counted or cut here: this only moves what the shaper decided into those shapes.
"""

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from ctviz.catalog.fields import CATALOG, BoundDimension
from ctviz.contract.plan import NodeKind
from ctviz.contract.response import Scalar
from ctviz.ctgov.study import Study, StudyDate
from ctviz.engine.frame import Cell, Frame
from ctviz.engine.lower import EnginePlan, ListRows, PointRows
from ctviz.engine.rows import RowsResult
from ctviz.engine.rows import TrialRow as RowOfTrial
from ctviz.engine.shape import ShapedFrame, ShapedResult
from ctviz.viz.shaped import NetworkEdge, NetworkNode, TrialRow
from ctviz.viz.shaped import ShapedResult as ForBuilders

_NODE_KINDS: Final[Mapping[str, NodeKind]] = {
    "sponsor": "sponsor",
    "drug": "drug",
    "condition": "condition",
    "country": "country",
}
# The dates a table row can show, by the key the builder looks up, and where the record keeps each.
_DATE_ATTRIBUTES: Final = {
    "start_date": "start_date",
    "completion_date": "completion_date",
    "first_posted_date": "first_post_date",
}


@dataclass(frozen=True)
class Presentation:
    """The builders' inputs: the plan with the window the shaper kept, and the shaped result."""

    plan: EnginePlan
    shaped: ForBuilders


def present(shaped: ShapedResult, plan: EnginePlan) -> Presentation:
    """Lay a shaped result out for the builders. The plan's window is replaced by the one that was kept."""
    window = shaped.window if shaped.window is not None else plan.window
    kept = dataclasses.replace(plan, window=window)
    nodes, edges = _network(shaped.frames, plan) if plan.relation == "network" else ((), ())
    return Presentation(
        kept,
        ForBuilders(
            frames=_frames(shaped),
            truncation=shaped.truncation,
            trials=tuple(trial for rows in shaped.rows for trial in _trials(rows, plan)),
            nodes=nodes,
            edges=edges,
            warnings=shaped.warnings,
        ),
    )


def _frames(shaped: ShapedResult) -> tuple[Frame, ...]:
    """One frame per scope, whose cells are the drawn marks; a per-trial plan has no cells."""
    drawn = [
        dataclasses.replace(item.frame, cells={cell.key: cell for cell in item.cells})
        for item in shaped.frames
    ]
    return tuple(drawn) + tuple(_rows_frame(rows) for rows in shaped.rows)


def _rows_frame(rows: RowsResult) -> Frame:
    return Frame(
        scope=rows.scope,
        dims=(),
        sample_size=0,
        matched=rows.matched,
        seen=rows.seen,
        analyzed=len(rows.rows),
        excluded=rows.excluded,
        strategy=rows.strategy,
        subset=rows.subset,
    )


# --- trials -----------------------------------------------------------------------------------------------


def _trials(rows: RowsResult, plan: EnginePlan) -> list[TrialRow]:
    return [_trial(row, plan) for row in rows.rows]


def _trial(row: RowOfTrial, plan: EnginePlan) -> TrialRow:
    study = row.study
    values: dict[str, Scalar] = {}
    match plan.rows:
        case PointRows(x=x, y=y):
            values = {x: _whole(row.x), y: _whole(row.y)}
        case ListRows():
            values = _listed(study)
    group = row.color.label if row.color is not None else None
    return TrialRow(study.nct_id, study.brief_title or "", values, row.evidence, group)


def _whole(number: float | None) -> int | float | None:
    """A count read from a record is a whole number; keep it one in the JSON."""
    return int(number) if number is not None and number.is_integer() else number


def _listed(study: Study) -> dict[str, Scalar]:
    values: dict[str, Scalar] = {
        "phase": _label(study, "phase"),
        "overall_status": _label(study, "overall_status"),
        "enrollment": study.enrollment_count,
        "lead_sponsor": study.lead_sponsor_name,
    }
    for key, attribute in _DATE_ATTRIBUTES.items():
        found: StudyDate | None = getattr(study, attribute)
        values[key] = found.date if found is not None else None
    return values


def _label(study: Study, key: str) -> str | None:
    spec = CATALOG[key]
    found = spec.extract(study, None, BoundDimension(spec, None, "axis"))
    return found[0].label if found else None


# --- networks ---------------------------------------------------------------------------------------------


def _network(
    frames: tuple[ShapedFrame, ...], plan: EnginePlan
) -> tuple[tuple[NetworkNode, ...], tuple[NetworkEdge, ...]]:
    """Nodes and links of the one scope a network is drawn for (a comparison is refused by the plan rules)."""
    if not frames:
        return (), ()
    frame = frames[0]
    kinds = [_node_kind(dimension) for dimension in plan.dimensions]
    one_kind = kinds[0] == kinds[1]
    sides: list[Mapping[str, NetworkNode]] = [
        {cell.key[0]: NetworkNode(kinds[side], cell.labels[0], cell) for cell in cells}
        for side, cells in enumerate(frame.nodes)
    ]
    ends = (sides[0], sides[0] if one_kind else sides[1])
    edges = tuple(
        NetworkEdge(ends[0][link.key[0]], ends[1][link.key[1]], link)
        for link in frame.cells
        if _has_both_ends(link, ends)
    )
    return tuple(node for side in sides for node in side.values()), edges


def _has_both_ends(link: Cell, ends: tuple[Mapping[str, NetworkNode], Mapping[str, NetworkNode]]) -> bool:
    return link.key[0] in ends[0] and link.key[1] in ends[1]


def _node_kind(dimension: BoundDimension) -> NodeKind:
    key = dimension.spec.key
    if key not in _NODE_KINDS:
        raise ValueError(f"A {key} cannot be a node of a network.")
    return _NODE_KINDS[key]
