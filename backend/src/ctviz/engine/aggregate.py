"""The one group-by: every aggregated analysis adds each trial once to every cell it belongs to.

Only the way a trial's values combine into cells varies: nothing for a single number, one cell per value
for one dimension, the cross product for two different dimensions, and unordered pairs of distinct
values when the same dimension is used twice (a network of one kind of node).
"""

import itertools
from collections.abc import Iterable, Iterator, Sequence
from typing import Final

from ctviz.catalog.fields import BoundDimension, FieldContexts, Value, Window
from ctviz.contract.plan import Pairing
from ctviz.ctgov.params import Scope
from ctviz.ctgov.study import Study
from ctviz.engine.evidence import citation_rank
from ctviz.engine.frame import Cell, Frame, TrialEvidence
from ctviz.engine.lower import EnginePlan

# Told apart because only the earlier trials are what a longer axis would show.
BEFORE_WINDOW: Final = ("before_window", "Before the periods shown")
AFTER_WINDOW: Final = ("after_window", "After the periods shown")


def missing_reason(dimension: BoundDimension) -> tuple[str, str]:
    """The exclusion of trials that have no value for a dimension, as (reason, message)."""
    return f"no_{dimension.spec.key}", f"No {dimension.spec.title.lower()} on record"


def cells(
    per_dim: Sequence[Sequence[Value]], dims: Sequence[BoundDimension], pairing: Pairing
) -> Iterator[tuple[Value, ...]]:
    """The cells one trial contributes to.

    No dimension: one empty cell (a single number). One dimension: one cell per value. Two different
    dimensions: the cross product. The same dimension twice: unordered pairs of distinct values.
    """
    if _is_one_dimension_twice(dims):
        for a, b in itertools.combinations(sorted(per_dim[0], key=lambda value: value.key), 2):
            if pairing == "same_trial" or (
                a.groups & b.groups
            ):  # same_arm: the two values share an arm label
                yield (a, b)
    else:
        yield from itertools.product(*per_dim)


def aggregate(
    studies: Iterable[Study], plan: EnginePlan, scope: Scope, contexts: FieldContexts, sample_size: int
) -> Frame:
    dims = plan.dimensions
    distinct = dims[:1] if _is_one_dimension_twice(dims) else dims
    frame = Frame(scope=scope, dims=dims, sample_size=sample_size)
    if len(distinct) < len(dims):
        frame.marginals = (frame.marginals[0], frame.marginals[0])  # one table serves both sides
    for study in studies:
        frame.seen += 1
        per_dim = [_values_or_missing(dim, study, contexts) for dim in distinct]
        if (reason := _exclusion(distinct, per_dim, plan.window)) is not None:
            frame.exclude(*reason)
            continue
        frame.analyzed += 1
        rank = citation_rank(study, scope)
        for table, values in zip(frame.marginals, per_dim, strict=False):
            for value in values:
                marginal = table.setdefault(value.key, Cell(key=(value.key,), labels=(value.label,)))
                marginal.add(TrialEvidence(study.nct_id, value.evidence, rank), sample_size)
        for combo in cells(per_dim, dims, plan.pairing):
            evidence = tuple(item for value in combo for item in value.evidence)
            cell = frame.cell(tuple(value.key for value in combo), tuple(value.label for value in combo))
            cell.add(TrialEvidence(study.nct_id, evidence, rank), sample_size)
    return frame


def _is_one_dimension_twice(dims: Sequence[BoundDimension]) -> bool:
    return len(dims) == 2 and dims[0].spec.key == dims[1].spec.key


def _values_or_missing(dimension: BoundDimension, study: Study, contexts: FieldContexts) -> list[Value]:
    """A trial's values for one dimension, each key once, in the missing bucket when it has none."""
    spec = dimension.spec
    merged: dict[str, Value] = {}
    for value in spec.extract(study, contexts.get(spec.key), dimension):
        known = merged.get(value.key)
        if known is not None:
            # Two interventions can name one drug: keep the evidence of both and the arms of both.
            value = Value(
                key=value.key,
                label=known.label,
                evidence=tuple(dict.fromkeys(known.evidence + value.evidence)),
                groups=known.groups | value.groups,
            )
        merged[value.key] = value
    if not merged and spec.missing is not None:
        merged[spec.missing.key] = Value(spec.missing.key, spec.missing.label, ())
    return list(merged.values())


def _exclusion(
    dims: Sequence[BoundDimension], per_dim: Sequence[Sequence[Value]], window: Window | None
) -> tuple[str, str] | None:
    """Why a trial cannot be placed, or None. Period labels sort as text within one unit."""
    for dimension, values in zip(dims, per_dim, strict=True):
        if not values:
            return missing_reason(dimension)
        bounds = window if dimension.spec.kind == "date" else None
        if bounds is not None and any(value.key < bounds.first for value in values):
            return BEFORE_WINDOW
        if bounds is not None and any(value.key > bounds.last for value in values):
            return AFTER_WINDOW
    return None
