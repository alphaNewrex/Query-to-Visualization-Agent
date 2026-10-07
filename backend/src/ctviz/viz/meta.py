"""The response around the drawing: `meta`, the visualization envelope and the three outcomes without a chart.

`build_response` is the one entry point after shaping: it chooses the type, builds it, and falls back to a
`no_data` answer when there is nothing to draw. Nothing here talks to the registry or the model.
"""

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Final, Literal

from ctviz.applied import MAX_COMPARED
from ctviz.catalog.states import StateScope
from ctviz.contract.plan import Clarify, QueryPlan, Unsupported
from ctviz.contract.request import CompareSpec, QueryRequest, RequestOptions
from ctviz.contract.response import (
    Adjustment,
    AppliedFilters,
    CacheInfo,
    CitationsInfo,
    Clarification,
    ClarificationReason,
    ClarificationResponse,
    Counts,
    Debug,
    EntityResolution,
    ExclusionCount,
    Interpretation,
    LabeledRequest,
    Measure,
    MessageResponse,
    Meta,
    Note,
    Outcome,
    PlannerInfo,
    ScopeEvidence,
    SeriesCounts,
    Source,
    StrategyStep,
    Timing,
    TraceStep,
    Truncation,
    VisualizationResponse,
)
from ctviz.ctgov.client import ApiVersion
from ctviz.engine.aggregate import BEFORE_WINDOW
from ctviz.engine.frame import Frame
from ctviz.engine.lower import EnginePlan, PointRows
from ctviz.viz import text
from ctviz.viz.build import Built, build_visualization, is_partial, scope_filters
from ctviz.viz.choose import ChartChoice, choose_chart
from ctviz.viz.citations import CitationBook
from ctviz.viz.shaped import ShapedResult

MIN_NETWORK_LINKS: Final = 3
WIDER_TOP_N: Final = 30
EARLIEST_YEAR: Final = 1900  # the lowest year a request accepts
_COMPARE_FIELDS: Final[dict[str, Literal["drug_name", "condition", "sponsor", "country"]]] = {
    "drug": "drug_name",
    "condition": "condition",
    "sponsor": "sponsor",
    "country": "country",
}


@dataclass(frozen=True)
class MetaContext:
    """What the stages before the builders know about the request, as `meta` reports it."""

    request_id: str
    generated_at: datetime
    query: str | None
    request: QueryRequest | None  # the follow-ups are copies of it
    filters: AppliedFilters
    plan: QueryPlan | None
    options: RequestOptions
    planner: PlannerInfo
    timing: Timing
    cache: CacheInfo
    adjustments: tuple[Adjustment, ...] = ()
    warnings: tuple[Note, ...] = ()
    assumptions: tuple[str, ...] = ()
    entities: tuple[EntityResolution, ...] = ()
    strategy: tuple[StrategyStep, ...] = ()
    source: Source | None = None
    trace: tuple[TraceStep, ...] | None = None  # None when options.include_trace is false


def build_response(
    context: MetaContext,
    plan: EnginePlan,
    shaped: ShapedResult,
    version: ApiVersion,
    *,
    titles: Mapping[str, str],
    scope_evidence: Mapping[str, Sequence[ScopeEvidence]] | None = None,
) -> VisualizationResponse | MessageResponse:
    """Choose, build and wrap; `no_data` when nothing can be drawn."""
    started = time.perf_counter()
    choice = choose_chart(plan, shaped)
    outcome = _nothing_to_draw(plan, shaped, choice)
    if outcome is not None:
        return _message_response(context, outcome, plan=plan, shaped=shaped)
    book = CitationBook(max_per_datum=plan.citations_per_datum, titles=titles, scope_evidence=scope_evidence)
    built = build_visualization(plan, shaped, choice, book, version)
    timed = _with_build_time(context, started)
    meta = build_meta(timed, plan=plan, shaped=shaped, version=version, choice=choice, built=built, book=book)
    return VisualizationResponse(
        message=built.message, visualization=built.visualization, references=book.references(), meta=meta
    )


def outcome_response(
    context: MetaContext,
    outcome: Outcome,
    *,
    plan: EnginePlan | None = None,
    shaped: ShapedResult | None = None,
) -> ClarificationResponse | MessageResponse:
    """A clarification, an unsupported answer or a no-data answer decided without drawing anything."""
    if outcome.kind == "clarification":
        if outcome.clarification is None:
            raise ValueError("A clarification outcome carries its clarification.")
        meta = build_meta(context, plan=plan, shaped=shaped, extra_warnings=outcome.warnings)
        return ClarificationResponse(message=outcome.message, clarification=outcome.clarification, meta=meta)
    return _message_response(context, outcome, plan=plan, shaped=shaped)


def clarification_outcome(
    reason: ClarificationReason,
    missing_fields: Sequence[str] = (),
    options: Sequence[LabeledRequest] = (),
) -> Outcome:
    """A question back to the user, in the service's own words and never the model's."""
    clarification = Clarification(reason=reason, missing_fields=list(missing_fields), options=list(options))
    return Outcome(
        "clarification", reason, text.clarification_question(reason, missing_fields), clarification
    )


def unsupported_outcome(category: str) -> Outcome:
    """A fixed sentence per category; the model's own reason stays in `meta.plan`."""
    return Outcome("unsupported", category, text.unsupported_message(category))


def _message_response(
    context: MetaContext, outcome: Outcome, *, plan: EnginePlan | None, shaped: ShapedResult | None
) -> MessageResponse:
    if outcome.kind == "clarification":
        raise ValueError("A clarification is not a message response.")
    meta = build_meta(context, plan=plan, shaped=shaped, extra_warnings=outcome.warnings)
    return MessageResponse(kind=outcome.kind, message=outcome.message, meta=meta)


def _nothing_to_draw(plan: EnginePlan, shaped: ShapedResult, choice: ChartChoice) -> Outcome | None:
    labels = [text.scope_name(frame.scope) for frame in shaped.frames]
    if choice.type == "network_graph" and len(shaped.edges) < MIN_NETWORK_LINKS:
        return Outcome(
            kind="no_data",
            reason="insufficient_cooccurrence",
            message=text.insufficient_cooccurrence_message(labels),
            warnings=(text.insufficient_cooccurrence(),),
        )
    is_empty = (
        not shaped.trials
        if choice.type in ("table", "scatter_plot") and choice.table_of is None
        else all(frame.analyzed == 0 for frame in shaped.frames)
    )
    if not is_empty:
        return None
    matched = (
        {frame.scope.label or "trials": frame.matched for frame in shaped.frames}
        if len(shaped.frames) > 1
        else None
    )
    return Outcome(kind="no_data", reason="matched_nothing", message=text.no_data_message(labels, matched))


def _with_build_time(context: MetaContext, started: float) -> MetaContext:
    milliseconds = round((time.perf_counter() - started) * 1000)
    return replace(context, timing=context.timing.model_copy(update={"build_ms": milliseconds}))


# --- Meta -------------------------------------------------------------------------------------------------


def build_meta(
    context: MetaContext,
    *,
    plan: EnginePlan | None = None,
    shaped: ShapedResult | None = None,
    version: ApiVersion | None = None,
    choice: ChartChoice | None = None,
    built: Built | None = None,
    book: CitationBook | None = None,
    extra_warnings: Sequence[Note] = (),
) -> Meta:
    """Everything about the answer other than what is drawn; the parts that need data are null without it."""
    counts, counts_warnings = _counts(shaped, built) if shaped is not None else (None, [])
    warnings = [*context.warnings, *extra_warnings, *counts_warnings]
    assumptions = list(context.assumptions)
    if plan is not None:
        assumptions.extend(_assumptions(plan, shaped))
        warnings.extend(_dimension_warnings(plan))
    if choice is not None:
        warnings.extend(choice.warnings)
    if shaped is not None:
        warnings.extend(shaped.warnings)
        warnings.extend(note for frame in shaped.frames for note in frame.warnings)
    if plan is not None and version is not None and choice is not None:
        partial = _partial_period(plan, choice, version)
        if partial is not None:
            warnings.append(partial)
    warnings = list(dict.fromkeys(warnings))
    assumptions = list(dict.fromkeys(assumptions))
    truncation_items = list(shaped.truncation) if shaped is not None else []
    citations_per_datum = book.max_per_datum if book is not None else context.options.citations_per_datum
    return Meta(
        request_id=context.request_id,
        generated_at=context.generated_at,
        query=context.query,
        filters=context.filters,
        interpretation=_interpretation(context, plan, choice) if plan is not None else None,
        plan=context.plan,
        options=context.options,
        planner=context.planner,
        assumptions=assumptions,
        warnings=warnings,
        source=context.source,
        counts=counts,
        truncation=Truncation(is_truncated=bool(truncation_items), items=truncation_items),
        citations=CitationsInfo(
            is_enabled=citations_per_datum > 0,
            max_per_datum=citations_per_datum,
            selection=text.citation_selection([step.name for step in context.strategy]),
            trials_cited=len(book.references()) if book is not None else 0,
        ),
        suggested_followups=_followups(context, plan, shaped),
        cache=context.cache,
        timing=context.timing,
        debug=Debug(trace=list(context.trace)) if context.trace is not None else None,
    )


def _counts(shaped: ShapedResult, built: Built | None) -> tuple[Counts, list[Note]]:
    """Per scope, `trials_matched` is what was counted, so the identity of rule 12 always holds."""
    several = len(shaped.frames) > 1
    series: list[SeriesCounts] = []
    warnings: list[Note] = []
    for frame in shaped.frames:
        label = frame.scope.label if several else None
        excluded = _exclusions(frame)
        counted = frame.analyzed + sum(item.count for item in excluded)
        if counted != frame.matched:
            warnings.append(text.counts_not_reconciled(label, frame.matched, counted))
        series.append(
            SeriesCounts(
                label=label, trials_matched=counted, trials_analyzed=frame.analyzed, trials_excluded=excluded
            )
        )
    data_points = built.data_points if built is not None else 0
    return Counts(
        data_points=data_points, series=series, trials_in_several_series=shaped.trials_in_several_series
    ), warnings


def _exclusions(frame: Frame) -> list[ExclusionCount]:
    return [
        ExclusionCount(reason=reason, count=item.count, message=item.message)
        for reason, item in frame.excluded.items()
    ]


def _interpretation(context: MetaContext, plan: EnginePlan, choice: ChartChoice | None) -> Interpretation:
    analysis = plan.public.analysis
    if isinstance(analysis, Clarify | Unsupported):
        raise ValueError("Only an analysis that ran has an interpretation.")
    groups = [dim.spec.key for dim in plan.dimensions]
    axis = plan.dimensions[0] if plan.dimensions else None
    unit = axis.time_unit if axis is not None and axis.spec.kind == "date" else None
    labels = [text.scope_name(scope) for scope in plan.scopes]
    counts_trials = analysis.kind in ("aggregate", "total", "network")
    filters = scope_filters(plan, plan.scopes[0])
    measure = plan.measure
    return Interpretation(
        summary=text.interpretation_summary(
            analysis.kind,
            groups,
            labels,
            unit,
            filters,
            None if measure is None else text.measure_label(measure.statistic, measure.field),
        ),
        analysis=analysis.kind,
        measure=_measure(plan) if counts_trials else None,
        group_by=groups,
        compare=_compare(plan),
        time_granularity=unit,
        counting_unit="trial",
        entities=list(context.entities),
        adjustments=list(context.adjustments),
        strategy=list(context.strategy),
        chart_rationale=choice.rationale if choice is not None else "",
    )


def _measure(plan: EnginePlan) -> Measure:
    measure = plan.measure
    if measure is None:
        return Measure(aggregate="count", of="trials", unit="trials")
    return Measure(aggregate=measure.statistic, of=measure.field, unit=text.NUMBER_UNITS[measure.field])


def _compare(plan: EnginePlan) -> CompareSpec | None:
    field = _COMPARE_FIELDS.get(plan.compare_kind or "")
    values = [entity.value for entity in plan.public.entities if entity.role == "compare"]
    if field is None or not 2 <= len(values) <= MAX_COMPARED:
        return None
    return CompareSpec(field=field, values=values)


def _partial_period(plan: EnginePlan, choice: ChartChoice, version: ApiVersion) -> Note | None:
    """A time axis whose last period contains the data date has not finished yet."""
    window = plan.window
    if (choice.table_of or choice.type) != "time_series" or window is None or not is_partial(window, version):
        return None
    return text.partial_period(window.last, date.fromisoformat(version.data_timestamp[:10]).isoformat())


def _assumptions(plan: EnginePlan, shaped: ShapedResult | None) -> list[str]:
    """The choices made where the question was open, stated for every dimension and scope that uses them."""
    notes = [note for dimension in plan.dimensions for note in dimension.spec.notes]
    filters = plan.public.filters
    if filters.phases:
        notes.append(text.PHASE_FILTER_NOTE)
    window = plan.window
    if window is not None:
        if filters.year_to is None:
            notes.append(text.no_end_period(window.last))
        if filters.year_from is None and shaped is not None and _left_before_window(shaped):
            notes.append(text.default_window(window.first))
    if plan.relation == "network":
        first, second = plan.dimensions[0].spec.key, plan.dimensions[1].spec.key
        notes.append(text.link_note(first, second, plan.pairing))
    if any(dimension.spec.key == "state" for dimension in plan.dimensions):
        notes.extend(_state_notes(plan))
    if plan.measure is not None:
        label = text.measure_label(plan.measure.statistic, plan.measure.field)
        notes.append(text.statistic_note(plan.measure.statistic, label))
        notes.append(text.MEASURE_NOTES[plan.measure.field])
    if isinstance(plan.rows, PointRows):
        notes.extend(text.NUMBER_NOTES[field] for field in dict.fromkeys((plan.rows.x, plan.rows.y)))
    if shaped is not None and shaped.trials_in_several_series:
        labels = [text.scope_name(scope) or scope.id for scope in plan.scopes]
        notes.append(text.overlap_note(shaped.trials_in_several_series, labels))
    return notes


def _state_notes(plan: EnginePlan) -> list[str]:
    """Which country's sites were grouped by state, and what the two shares of a state chart are of."""
    stated = [StateScope.of(scope).countries for scope in plan.scopes]
    countries = tuple(dict.fromkeys(name for names in stated for name in names))
    notes = [text.state_sites_note(countries, is_open=any(not names for names in stated))]
    if len(plan.dimensions) == 1 and len(plan.scopes) == 1 and plan.measure is None:
        notes.append(text.STATE_SHARE_NOTE)
    return notes


def _left_before_window(shaped: ShapedResult) -> bool:
    reason = BEFORE_WINDOW[0]
    return any(reason in frame.excluded and frame.excluded[reason].count for frame in shaped.frames)


def _dimension_warnings(plan: EnginePlan) -> list[Note]:
    """The caveats that belong to a field whenever it is used: free text is only partly merged."""
    keys = {dimension.spec.key for dimension in plan.dimensions}
    return [
        note
        for key, note in (
            ("drug", text.names_partly_normalised()),
            ("condition", text.free_text_categories()),
        )
        if key in keys
    ]


def _followups(
    context: MetaContext, plan: EnginePlan | None, shaped: ShapedResult | None
) -> list[LabeledRequest]:
    """Rules, never the model: a cut list offers more, a window that cut trials off offers all years, an
    ambiguous sponsor offers each full name."""
    request = context.request
    if request is None:
        return []
    followups: list[LabeledRequest] = []
    cut = shaped is not None and any(item.scope == "categories" for item in shaped.truncation)
    if cut and (request.top_n or 0) < WIDER_TOP_N:
        followups.append(
            LabeledRequest(
                label=text.show_more(WIDER_TOP_N), request=request.model_copy(update={"top_n": WIDER_TOP_N})
            )
        )
    is_yearly = plan is not None and plan.window is not None and plan.window.unit == "year"
    if is_yearly and shaped is not None and request.start_year is None and _left_before_window(shaped):
        followups.append(
            LabeledRequest(
                label=text.SHOW_ALL_YEARS, request=request.model_copy(update={"start_year": EARLIEST_YEAR})
            )
        )
    for entity in context.entities:
        if entity.kind == "sponsor" and entity.status == "ambiguous":
            followups.extend(
                LabeledRequest(
                    label=text.sponsor_option(candidate.value),
                    request=request.model_copy(update={"sponsor": [candidate.value]}),
                )
                for candidate in entity.candidates[:3]
            )
    return followups
