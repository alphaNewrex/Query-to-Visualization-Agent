"""The response contract: the envelope, the seven visualization types, their channels, and `meta`.

Rules that hold everywhere (section 5.1 of the plan):

- Every key is always present: `null` means not applicable and arrays are never null. Responses are
  serialized with `model_dump(mode="json")`, never with `exclude_none`, which would delete meaningful
  nulls such as a citation's `excerpt: null`.
- `json_schema_serialization_defaults_required` lists every key as required in the exported schema, so
  the generated response types have no optional keys. The request models that responses embed keep
  theirs (`RequestOptions` in `meta.options`, `QueryRequest` in follow-ups).
- Data is render-ready: flat rows, one row per mark, already aggregated, binned, sorted, zero-filled and
  truncated. No colours are sent; colour index i belongs to `domain[i]`.
- Every string is plain text. Names of sponsors, drugs and trials are third-party text.
"""

from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from ctviz.contract.plan import (
    AgeGroup,
    Allocation,
    DateField,
    EntityKind,
    InterventionModel,
    InterventionType,
    Masking,
    Phase,
    PrimaryPurpose,
    QueryPlan,
    ResultsPosted,
    Sex,
    SponsorClass,
    Status,
    StudyType,
    TimeUnit,
)
from ctviz.contract.request import CompareSpec, QueryRequest, RequestOptions

Scalar = str | int | float | bool | None
NumberFormat = Literal[",d", ".1f", ".1%"]
Origin = Literal["resolution", "probe", "execution"]
MatchDefinition = Literal[
    "intervention_search",
    "intervention_name",
    "condition_search",
    "lead_sponsor_search",
    "country_exact",
    "term_search",
]
StrategyName = Literal["sorted_page", "walk", "count_fan_out", "sample_then_recount", "none"]
ClarificationReason = Literal[
    "missing_entity", "unknown_value", "ambiguous_request", "could_not_interpret", "too_broad"
]
ErrorCodeName = Literal[
    "invalid_request",
    "not_found",
    "method_not_allowed",
    "planner_unavailable",
    "upstream_unavailable",
    "upstream_rate_limited",
    "upstream_timeout",
    "deadline_exceeded",
    "internal_error",
    "backend_unreachable",  # only the frontend's proxy emits this one
]


class Model(BaseModel):
    """Base of every response model: unknown keys are forbidden and every key is listed as required."""

    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


# --- Citations and data rows ------------------------------------------------------------------------


class Citation(Model):
    """A pointer into the trial record that a value was computed from."""

    nct_id: str = Field(pattern=r"^NCT\d{8}$", description="Key into the top-level `references` map.")
    field: str = Field(
        description="Path in `GET /api/v2/studies/{nct_id}` with [i] for list items, addressing one value."
    )
    excerpt: str | None = Field(
        description="The value at that path, verbatim; numbers and booleans in JSON notation. Null means "
        "the field is absent and the absence is the evidence."
    )


class Datum(BaseModel):
    """A data row: the fields named by the encoding, plus the three reserved keys below.

    The one contract model that allows extra keys, restricted to scalars. A renderer reads a field
    through its encoding, and never sorts, filters or computes.
    """

    model_config = ConfigDict(extra="allow", json_schema_serialization_defaults_required=True)
    __pydantic_extra__: dict[str, Scalar] = Field(init=False)

    citations: list[Citation] = Field(
        default_factory=list, description="Evidence for at most `max_per_datum` trials; [] when disabled."
    )
    citation_count: int = Field(ge=0, description="Distinct trials behind this datum; never null.")
    source_url: str | None = Field(
        None, description="An API URL that returns exactly those trials, or null when none can."
    )


class Node(Datum):
    """A network node: `label`, `entity_type` and `trial_count` are its other keys."""

    id: str


class Edge(Datum):
    """A network link: `trial_count` is its other key."""

    id: str
    source: str
    target: str


# --- Channels ---------------------------------------------------------------------------------------


class ChannelSort(Model):
    """How a category axis was ordered. Informational: `domain` and row order are authoritative."""

    by: Literal["value", "label", "natural"]
    order: Literal["ascending", "descending"]


class CategoryChannel(Model):
    """A channel of labelled values; colour index i belongs to `domain[i]`."""

    field: str
    type: Literal["nominal", "ordinal"]
    title: str
    domain: list[str] = Field(description="Every value in display order: axis, legend and colour index.")
    sort: ChannelSort | None
    is_exclusive: bool = Field(
        description="false: a trial can fall under several values, so values add up to more than the "
        "trials; never draw such a channel as parts of a whole."
    )


class TemporalChannel(Model):
    """A time axis. Labels are YYYY, YYYY-Qn or YYYY-MM, ascending with no gaps; never parse them as dates."""

    field: str
    type: Literal["temporal"]
    title: str
    time_unit: TimeUnit


class QuantitativeChannel(Model):
    """A numeric channel."""

    field: str
    type: Literal["quantitative"]
    title: str
    unit: str | None = Field(description="A plural noun: trials, participants, months, sites.")
    format: NumberFormat | None = Field(description="The closed set of number formats of v1.")
    scale: Literal["linear", "log"] = Field(description="log only when every value is above zero.")


class FieldDef(Model):
    """A displayed field: a tooltip entry or a table column."""

    field: str
    title: str
    type: Literal["nominal", "ordinal", "quantitative", "temporal"]
    unit: str | None
    format: NumberFormat | None
    href_field: str | None = Field(description="A row key holding a URL to link this value to.")


class FieldRef(Model):
    """A reference to a row key."""

    field: str


# --- The seven visualization types ------------------------------------------------------------------


class BarChartEncoding(Model):
    x: CategoryChannel
    y: QuantitativeChannel
    series: CategoryChannel | None
    tooltip: list[FieldDef]


class BarChart(Model):
    """One row per (x, series), following `x.domain`; with a series every combination is present."""

    type: Literal["bar_chart"]
    title: str
    subtitle: str | None = Field(
        description="Scope, number of trials, data date and any subset the chart is limited to."
    )
    orientation: Literal["vertical", "horizontal"] = Field(
        description="A drawing hint; x is the category axis either way."
    )
    stack: Literal["none", "stacked"] = Field(
        description="A grouped bar chart is `series` set with `none`; `stacked` only for an exclusive series."
    )
    encoding: BarChartEncoding
    data: list[Datum]


class TimeSeriesEncoding(Model):
    x: TemporalChannel
    y: QuantitativeChannel
    series: CategoryChannel | None
    tooltip: list[FieldDef]


class TimeSeries(Model):
    """One row per (period, series), ascending and zero-filled."""

    type: Literal["time_series"]
    title: str
    subtitle: str | None
    mark: Literal["line", "bar", "area"]
    stack: Literal["none", "stacked"] = Field(description="Always `none` for lines.")
    encoding: TimeSeriesEncoding
    data: list[Datum]


class HistogramEncoding(Model):
    x: QuantitativeChannel = Field(description="Bin start, inclusive.")
    x2: FieldRef = Field(description="Bin end, exclusive; null in the last row means open-ended.")
    y: QuantitativeChannel
    label: FieldRef
    tooltip: list[FieldDef]


class Histogram(Model):
    """One row per bin, contiguous and ascending. Bins may be uneven: draw one bar per row using the label."""

    type: Literal["histogram"]
    title: str
    subtitle: str | None
    encoding: HistogramEncoding
    data: list[Datum]


class ScatterPlotEncoding(Model):
    x: QuantitativeChannel
    y: QuantitativeChannel
    series: CategoryChannel | None
    size: QuantitativeChannel | None
    label: FieldDef | None
    tooltip: list[FieldDef]


class ScatterPlot(Model):
    """One row per trial; each row cites itself with the fields its coordinates came from."""

    type: Literal["scatter_plot"]
    title: str
    subtitle: str | None
    encoding: ScatterPlotEncoding
    data: list[Datum]


class NetworkNodeEncoding(Model):
    label: FieldRef
    color: CategoryChannel | None = Field(
        description="With the `bipartite` layout its domain has exactly two values, the two sides."
    )
    size: QuantitativeChannel | None = Field(description="Map to node area.")
    tooltip: list[FieldDef]


class NetworkEdgeEncoding(Model):
    weight: QuantitativeChannel | None = Field(description="Map to stroke width.")
    tooltip: list[FieldDef]


class NetworkEncoding(Model):
    nodes: NetworkNodeEncoding
    edges: NetworkEdgeEncoding


class NetworkData(Model):
    nodes: list[Node]
    edges: list[Edge]


class NetworkGraph(Model):
    """Nodes and links; `source` and `target` of a link are node ids."""

    type: Literal["network_graph"]
    title: str
    subtitle: str | None
    is_directed: Literal[False]
    layout: Literal["force", "bipartite"]
    encoding: NetworkEncoding
    data: NetworkData


class TableEncoding(Model):
    columns: list[FieldDef] = Field(description="In display order.")


class Table(Model):
    """One row per record; `href_field` names a row key holding a URL."""

    type: Literal["table"]
    title: str
    subtitle: str | None
    encoding: TableEncoding
    data: list[Datum]


class MetricEncoding(Model):
    value: QuantitativeChannel


class Metric(Model):
    """A single number, with citations and `source_url` like any datum."""

    type: Literal["metric"]
    title: str
    subtitle: str | None
    encoding: MetricEncoding
    data: list[Datum] = Field(min_length=1, max_length=1, description="Exactly one row.")


type Visualization = Annotated[
    BarChart | TimeSeries | Histogram | ScatterPlot | NetworkGraph | Table | Metric,
    Field(discriminator="type"),
]


# --- Metadata ---------------------------------------------------------------------------------------


class Note(Model):
    """A data-quality or completeness caveat; `code` is one of the stable warning codes."""

    model_config = ConfigDict(frozen=True)

    code: str
    message: str


class Adjustment(Model):
    """A change the service made to the plan, and what it did."""

    model_config = ConfigDict(frozen=True)

    code: str
    path: str
    message: str
    action: Literal["dropped", "replaced", "clamped", "defaulted", "repaired", "kept"]


class Usage(Model):
    """Token counts of one model call."""

    model_config = ConfigDict(frozen=True)

    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cached_tokens: int


class PlannerInfo(Model):
    """How the plan was produced."""

    mode: Literal["llm", "structured", "supplied_plan"]
    model: str | None = Field(description="The snapshot the model alias resolved to.")
    reasoning_effort: str | None
    prompt_version: str | None
    attempts: int
    is_repaired: bool
    is_fallback: bool
    usage: Usage | None


class AppliedExclusions(Model):
    """What the answer leaves out, in request field names (`exclude` of the request)."""

    drug_name: list[str]
    condition: list[str]
    sponsor: list[str]
    country: list[str]
    term: list[str]
    status: list[Status]


class AppliedFilters(Model):
    """The effective scope in request field names: it can be sent back as request fields."""

    drug_name: list[str]
    condition: list[str]
    sponsor: list[str]
    country: list[str]
    term: list[str]
    trial_phase: list[Phase]
    status: list[Status]
    study_type: list[StudyType]
    sponsor_class: list[SponsorClass]
    intervention_type: list[InterventionType]
    sex: list[Sex]
    age_group: list[AgeGroup]
    allocation: list[Allocation]
    masking: list[Masking]
    primary_purpose: list[PrimaryPurpose]
    has_results: list[ResultsPosted]
    intervention_model: list[InterventionModel]
    start_year: int | None
    end_year: int | None
    date_field: DateField | None
    compare: CompareSpec | None
    exclude: AppliedExclusions


class OtherReading(Model):
    """The count if the words were read as another kind of entity."""

    kind: EntityKind
    trials_matched: int


class SponsorCandidate(Model):
    """A distinct lead-sponsor name in a 200-row sample, with its share of the sample."""

    value: str
    sample_share: float


class RegistryTerm(Model):
    """The vocabulary term the registry itself most often assigns to the trials a wording matched."""

    term: str = Field(description="A MeSH term as the registry's records list it.")
    trials_matched: int = Field(description="The trials that carry this term in the registry.")
    sample_share: float = Field(description="The share of the sampled matching trials that carry it.")


class EntityResolution(Model):
    """What the registry made of one entity of the plan."""

    kind: EntityKind
    planned_kind: EntityKind = Field(
        description="The kind the plan named. It differs from `kind` when the registry's counts decided "
        "which of drug, condition or term the words are."
    )
    source: Literal["question", "request_field", "plan"]
    text: str = Field(description="The words used.")
    term_searched: str = Field(description="After `essie.literal()`, or the registry's country name.")
    definition: MatchDefinition
    status: Literal["ok", "low_match", "ambiguous", "no_match", "matches_everything"]
    trials_matched: int
    strict_name_matches: int | None = Field(
        description="Drug, condition and term readings: the count of `AREA[InterventionName]`."
    )
    condition_name_matches: int | None = Field(
        description="Drug, condition and term readings: the count of `AREA[Condition]`."
    )
    registry_term: RegistryTerm | None = Field(
        description="Drug and condition readings: the registry's own term for the trials found, "
        "when it covers far more trials than the wording did."
    )
    other_readings: list[OtherReading]
    candidates: list[SponsorCandidate] = Field(description="Sponsors only.")


class Measure(Model):
    aggregate: Literal["count", "median", "mean", "sum"]
    of: Literal["trials", "enrollment", "duration_months", "site_count"]
    unit: str = Field(description="What `of` is counted in: trials, participants, months or sites.")


class StrategyStep(Model):
    """How the trials of one series were fetched."""

    series: str | None
    name: StrategyName
    reason: str
    upstream_requests: int


class Interpretation(Model):
    """How the question was read, resolved and executed."""

    summary: str = Field(description="Written by code from the plan, so it describes what was run.")
    analysis: Literal["aggregate", "total", "relate", "network", "trial_list"]
    measure: Measure | None = Field(description="Null for relate and trial_list.")
    group_by: list[str] = Field(
        description="The first is the axis, the second the series; node kinds for a network."
    )
    compare: CompareSpec | None
    time_granularity: TimeUnit | None
    counting_unit: Literal["trial"]
    entities: list[EntityResolution]
    adjustments: list[Adjustment]
    strategy: list[StrategyStep]
    chart_rationale: str = Field(description="The rule of the visualization-type table that fired.")


class UpstreamRequest(Model):
    """One request to ClinicalTrials.gov, in the order issued."""

    method: Literal["GET"] = "GET"
    url: str
    status: int
    duration_ms: int
    total_count: int | None
    records_returned: int | None
    is_cached: bool
    origin: Origin


class Source(Model):
    """The registry the data came from, and every request made to it."""

    name: Literal["ClinicalTrials.gov"] = "ClinicalTrials.gov"
    url: Literal["https://clinicaltrials.gov"] = "https://clinicaltrials.gov"
    api_version: str = Field(description="From `GET /version`, verbatim.")
    data_timestamp: str = Field(description="From `GET /version`, verbatim; it has no offset.")
    retrieved_at: AwareDatetime
    study_url_template: Literal["https://clinicaltrials.gov/study/{nct_id}"] = (
        "https://clinicaltrials.gov/study/{nct_id}"
    )
    record_url_template: Literal["https://clinicaltrials.gov/api/v2/studies/{nct_id}"] = (
        "https://clinicaltrials.gov/api/v2/studies/{nct_id}"
    )
    fhir_url_template: Literal["https://clinicaltrials.gov/api/v2/studies/{nct_id}?format=fhir.json"] = (
        "https://clinicaltrials.gov/api/v2/studies/{nct_id}?format=fhir.json"
    )
    requests: list[UpstreamRequest]


class ExclusionCount(Model):
    """Trials of a series that were not drawn, and why."""

    reason: str
    count: int
    message: str


class SeriesCounts(Model):
    """`trials_matched = trials_analyzed + the sum of trials_excluded counts` always holds."""

    label: str | None = Field(description="Null for a single scope.")
    trials_matched: int
    trials_analyzed: int
    trials_excluded: list[ExclusionCount]


class Counts(Model):
    data_points: int = Field(description="Rows, or nodes plus links.")
    series: list[SeriesCounts]
    trials_in_several_series: int | None = Field(description="Filled for a two-group comparison.")


class TruncationItem(Model):
    scope: Literal["categories", "series", "periods", "nodes", "edges", "points", "rows"]
    shown: int
    total: int
    rule: str


class Truncation(Model):
    is_truncated: bool
    items: list[TruncationItem]


class CitationsInfo(Model):
    is_enabled: bool
    max_per_datum: int
    selection: str = Field(description="Which trials are cited, as the strategy chose them.")
    trials_cited: int = Field(description="The size of `references`.")


class LabeledRequest(Model):
    """A complete request body a button can post; built by rules, never by the model."""

    label: str
    request: QueryRequest


class CacheInfo(Model):
    is_plan_cached: bool
    is_response_cached: bool
    cached_at: AwareDatetime | None = Field(
        description="When a cached response was first built; null on a fresh one."
    )


class Timing(Model):
    total_ms: int
    plan_ms: int
    resolve_ms: int
    fetch_ms: int
    build_ms: int


class TraceStep(Model):
    """One step of the run. `detail` is outside the stability promise."""

    index: int
    type: Literal["plan", "validate", "repair", "resolve_entity", "probe", "strategy", "execute", "build"]
    summary: str
    duration_ms: int
    request_indexes: list[int] = Field(description="Indexes into `meta.source.requests`.")
    detail: dict[str, Any]


class Debug(Model):
    trace: list[TraceStep]


class Meta(Model):
    """Everything about the answer other than what is drawn. A renderer needs nothing from it."""

    request_id: str
    generated_at: AwareDatetime
    query: str | None = Field(
        description="As sent, after whitespace normalisation; null for `POST /v1/analyses`."
    )
    filters: AppliedFilters
    interpretation: Interpretation | None = Field(description="Null when no plan could be produced.")
    plan: QueryPlan | None = Field(description="The canonical plan, defaults explicit.")
    options: RequestOptions = Field(
        description="The effective options; `{plan, options}` posted to `/v1/analyses` replays the answer."
    )
    planner: PlannerInfo
    assumptions: list[str] = Field(description="Plain-language choices made where the question was open.")
    warnings: list[Note]
    source: Source | None = Field(description="Null when no upstream request was made.")
    counts: Counts | None
    truncation: Truncation
    citations: CitationsInfo
    suggested_followups: list[LabeledRequest]
    cache: CacheInfo
    timing: Timing
    debug: Debug | None = Field(description="Null when `options.include_trace` is false.")


# --- The envelope -----------------------------------------------------------------------------------


class ScopeEvidence(Model):
    """Why a trial is in scope: the first fetched field that contains an entity's words."""

    entity_kind: EntityKind
    entity_text: str = Field(description="The user's words.")
    field: str | None = Field(description="Path of the first fetched field that contains them.")
    excerpt: str | None = Field(
        description="The value at that path; both null means matched by the registry's own search."
    )


class TrialReference(Model):
    title: str = Field(description="The brief title, verbatim.")
    url: str = Field(description="https://clinicaltrials.gov/study/{nct_id}")
    scope_evidence: list[ScopeEvidence]


class Clarification(Model):
    reason: ClarificationReason
    missing_fields: list[str] = Field(
        description="Names of request fields that would settle it; may be empty."
    )
    options: list[LabeledRequest] = Field(
        description="Complete request bodies a button can post; may be empty."
    )


@dataclass(frozen=True)
class Outcome:
    """An answer with nothing to draw, decided before a visualization could be built.

    `reason` is the clarification reason, the unsupported category or the no-data reason, by `kind`.
    """

    kind: Literal["clarification", "unsupported", "no_data"]
    reason: str
    message: str
    clarification: Clarification | None = None
    warnings: tuple[Note, ...] = ()


class VisualizationResponse(Model):
    """An answer drawn as a chart, a number or a table."""

    spec_version: Literal["1.0"] = "1.0"
    kind: Literal["visualization"] = "visualization"
    message: str = Field(description="A one-sentence headline computed from the data.")
    visualization: Visualization
    clarification: None = None
    references: dict[str, TrialReference] = Field(
        description="Exactly the trials cited in the data, keyed by NCT ID."
    )
    meta: Meta


class ClarificationResponse(Model):
    """The service needs a name or a choice before it can answer."""

    spec_version: Literal["1.0"] = "1.0"
    kind: Literal["clarification"] = "clarification"
    message: str
    visualization: None = None
    clarification: Clarification
    references: dict[str, TrialReference] = Field(default_factory=dict)
    meta: Meta


class MessageResponse(Model):
    """Nothing to draw: `no_data` when the plan ran and matched nothing, `unsupported` when it cannot run."""

    spec_version: Literal["1.0"] = "1.0"
    kind: Literal["no_data", "unsupported"]
    message: str
    visualization: None = None
    clarification: None = None
    references: dict[str, TrialReference] = Field(default_factory=dict)
    meta: Meta


type QueryResponse = Annotated[
    VisualizationResponse | ClarificationResponse | MessageResponse, Field(discriminator="kind")
]


class ErrorBody(Model):
    code: ErrorCodeName
    message: str
    details: dict[str, Any] = Field(
        description="`errors[]` of {path, code, message} for invalid_request; `reason` for "
        "planner_unavailable; otherwise empty."
    )
    request_id: str
    is_retryable: bool


class ErrorResponse(Model):
    """The body of every non-2xx response, from the backend and from the frontend's proxy."""

    error: ErrorBody
