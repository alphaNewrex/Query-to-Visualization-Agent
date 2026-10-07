"""The count fan-out: one request per bucket, at any scope size.

Every bucket of every closed dimension has a server expression, so the registry counts the trials of each
cell itself. One sample call per cell returns the exact count together with a few records to cite. The
result is the same `Frame` that a walk fills, so one builder serves both executors.
"""

import itertools
from collections.abc import Awaitable, Callable, Sequence
from datetime import date, timedelta
from typing import Final

from ctviz.catalog.fields import BoundDimension, Bucket, Evidence, Value, Window
from ctviz.catalog.periods import period_range
from ctviz.contract.response import Note
from ctviz.ctgov import essie
from ctviz.ctgov.client import RequestLog
from ctviz.ctgov.essie import Expr
from ctviz.ctgov.params import Scope
from ctviz.ctgov.study import Study
from ctviz.engine.aggregate import AFTER_WINDOW, BEFORE_WINDOW, missing_reason
from ctviz.engine.evidence import citation_rank, projection, scope_evidence
from ctviz.engine.frame import Cell, Frame, TrialEvidence
from ctviz.engine.gather import gather
from ctviz.engine.lower import EnginePlan
from ctviz.engine.registry import Registry

# A field whose every trial has a value, so that no request is spent on counting the ones without.
_NEVER_MISSING: Final = frozenset({"first_posted_date", "overall_status", "has_results"})
Probe = tuple[str, str, Expr]  # exclusion reason, its message, the expression selecting those trials


def fan_out_bill(plan: EnginePlan, window: Window | None) -> int | None:
    """How many requests a fan-out of the plan needs, or None when some bucket has no server expression."""
    if plan.measure is not None:
        return None  # a count call cannot give a median, a mean or a sum
    lists = _bucket_lists(plan, window)
    if lists is None:
        return None
    calls = len(list(itertools.product(*lists)))
    return sum(calls + len(_probes(plan, scope, window)) for scope in plan.scopes)


async def fan_out(
    scope: Scope,
    plan: EnginePlan,
    *,
    matched: int,
    window: Window | None,
    client: Registry,
    ctx: RequestLog,
) -> Frame:
    """Count every cell of one scope with one request each, and the trials that fit no cell."""
    lists = _bucket_lists(plan, window)
    if lists is None:
        raise ValueError("A fan-out needs a server expression for every bucket.")
    size = plan.citations_per_datum
    fields = projection(plan, scope)
    sort = "@relevance" if scope.terms else "StudyFirstPostDate:desc"
    frame = Frame(
        scope=scope, dims=plan.dimensions, sample_size=size, matched=matched, strategy="count_fan_out"
    )

    def cell_call(combo: tuple[Bucket, ...]) -> Callable[[], Awaitable[Cell]]:
        return lambda: _count_cell(combo, scope, plan, fields, sort, client, ctx)

    for cell in await gather([cell_call(combo) for combo in itertools.product(*lists)]):
        frame.cells[cell.key] = cell
    probes = _probes(plan, scope, window)
    probe_counts = await gather([_probe_call(scope, expr, client, ctx) for _, _, expr in probes])
    for (reason, message, _), count in zip(probes, probe_counts, strict=True):
        if count:
            frame.exclude(reason, message, count)
    _finish(frame, plan)
    return frame


async def _count_cell(
    combo: tuple[Bucket, ...],
    scope: Scope,
    plan: EnginePlan,
    fields: Sequence[str],
    sort: str,
    client: Registry,
    ctx: RequestLog,
) -> Cell:
    expr = essie.and_(*(bucket.expr for bucket in combo if bucket.expr is not None)) if combo else None
    params = scope.params() if expr is None else scope.params().narrowed_by(expr)
    size = plan.citations_per_datum
    page = await client.sample(
        params, ctx, fields=fields, page_size=max(1, size), sort=sort, origin="execution"
    )
    cell = Cell(
        key=tuple(bucket.key for bucket in combo),
        labels=tuple(bucket.label for bucket in combo),
        trials=page.total,
        expr=expr,
        source_url=page.url,
    )
    for study in page.studies[:size]:
        if (evidence := _evidence(study, combo, plan, scope)) is not None:
            cell.sample.append(TrialEvidence(study.nct_id, evidence, citation_rank(study, scope)))
    return cell


def _evidence(
    study: Study, combo: tuple[Bucket, ...], plan: EnginePlan, scope: Scope
) -> tuple[Evidence, ...] | None:
    """What to cite for a trial of a cell: its own values of the cell's buckets; None if it has none."""
    if not combo:
        return scope_evidence(study, scope)
    evidence: list[Evidence] = []
    for dimension, bucket in zip(plan.dimensions, combo, strict=True):
        found = _value_of(study, dimension, bucket)
        if found is None:
            return None
        evidence.extend(found.evidence)
    return tuple(evidence)


def _value_of(study: Study, dimension: BoundDimension, bucket: Bucket) -> Value | None:
    return next(
        (value for value in dimension.spec.extract(study, None, dimension) if value.key == bucket.key), None
    )


def _bucket_lists(plan: EnginePlan, window: Window | None) -> list[Sequence[Bucket]] | None:
    lists: list[Sequence[Bucket]] = []
    for dimension in plan.dimensions:
        if dimension.spec.buckets is None:
            return None
        buckets = dimension.spec.buckets(dimension, window)
        if any(bucket.expr is None for bucket in buckets):
            return None
        lists.append(buckets)
    return lists


def _probes(plan: EnginePlan, scope: Scope, window: Window | None) -> list[Probe]:
    """Counts of the trials that fit no bucket: before or after the window, or with no value at all."""
    probes: list[Probe] = []
    for dimension in plan.dimensions:
        spec = dimension.spec
        piece = spec.pieces[0]
        if spec.kind == "date" and window is not None:
            probes.extend(_window_probes(scope, piece, window))
        # A date range on the same field already leaves out the trials that have no date.
        is_ranged = scope.date_range is not None and scope.date_range.piece == piece
        if (
            spec.missing is None
            and spec.key not in _NEVER_MISSING
            and (spec.kind != "category" or not spec.is_exclusive)
            and not is_ranged
        ):
            reason, message = missing_reason(dimension)
            probes.append((reason, message, essie.missing(piece)))
    return probes


def _window_probes(scope: Scope, piece: str, window: Window) -> list[Probe]:
    first = date.fromisoformat(period_range(window.first)[0])
    last = date.fromisoformat(period_range(window.last)[1])
    own = scope.date_range if scope.date_range is not None and scope.date_range.piece == piece else None
    probes: list[Probe] = []
    # A scope that is already limited to the window's side has nothing there to count.
    if own is None or own.first_day is None or own.first_day < first.isoformat():
        probes.append((*BEFORE_WINDOW, essie.range_(piece, None, (first - timedelta(days=1)).isoformat())))
    if own is None or own.last_day is None or own.last_day > last.isoformat():
        probes.append((*AFTER_WINDOW, essie.range_(piece, (last + timedelta(days=1)).isoformat(), None)))
    return probes


def _probe_call(scope: Scope, expr: Expr, client: Registry, ctx: RequestLog) -> Callable[[], Awaitable[int]]:
    return lambda: client.count(scope.params().narrowed_by(expr), ctx, origin="execution")


def _finish(frame: Frame, plan: EnginePlan) -> None:
    """Set the counters and marginals from the counted cells, and check that the counts reconcile."""
    excluded = sum(item.count for item in frame.excluded.values())
    counted = sum(cell.trials for cell in frame.cells.values())
    frame.seen = frame.matched
    frame.analyzed = frame.matched - excluded
    if plan.dimensions and all(dimension.spec.is_exclusive for dimension in plan.dimensions):
        # The cells partition the trials, so cells plus exclusions must equal the probe's count. A data
        # refresh during the request is the benign cause of a difference.
        frame.analyzed = counted
        if counted + excluded != frame.matched:
            frame.matched = counted + excluded
            frame.seen = frame.matched
            frame.warnings.append(
                Note(
                    code="counts_not_reconciled",
                    message="The counts of the groups do not add up to the number of matching trials, "
                    "probably because the registry's data changed during the request.",
                )
            )
    _fill_marginals(frame)


def _fill_marginals(frame: Frame) -> None:
    """Totals per value of each dimension, where summing the other dimension's cells counts a trial once."""
    dims = frame.dims
    for position, table in enumerate(frame.marginals):
        others = [dim for index, dim in enumerate(dims) if index != position]
        if not all(dim.spec.is_exclusive for dim in others):
            continue
        for cell in frame.cells.values():
            key, label = cell.key[position], cell.labels[position]
            total = table.setdefault(key, Cell(key=(key,), labels=(label,)))
            total.trials += cell.trials
            if len(dims) == 1:
                total.sample, total.expr, total.source_url = list(cell.sample), cell.expr, cell.source_url
