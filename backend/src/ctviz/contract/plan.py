"""The query plan: what the planner model writes, what `POST /v1/analyses` accepts, what a response echoes.

Written for OpenAI strict structured outputs, as measured live on every allowed model: every field is
required and has no default (optional means `X | None`), and the tagged union is a plain union of
variants that each carry a `Literal` tag. A `Field(discriminator=...)` union, `dict`, `set` and `tuple`
fields are all rejected with HTTP 400. Output follows key order, so the short free-text field comes first.
No field can hold a data value, a row, a title or an NCT ID.

Strict mode enforces a numeric or length bound by silently changing the value, so a bound here is not a
validator: the bounds are loose on purpose and `planning/validate.py` clamps with a recorded adjustment.

The classes carry comments and no docstrings: a docstring becomes a `description` in the schema the
model receives, and the schema is measured and kept as small as it is.
"""

from dataclasses import dataclass
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field


class PlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


EntityKind = Literal["drug", "condition", "sponsor", "country", "term"]
Phase = Literal["EARLY_PHASE1", "PHASE1", "PHASE2", "PHASE3", "PHASE4", "NA"]
Status = Literal[
    "NOT_YET_RECRUITING",
    "RECRUITING",
    "ENROLLING_BY_INVITATION",
    "ACTIVE_NOT_RECRUITING",
    "COMPLETED",
    "SUSPENDED",
    "TERMINATED",
    "WITHDRAWN",
    "AVAILABLE",
    "NO_LONGER_AVAILABLE",
    "TEMPORARILY_NOT_AVAILABLE",
    "APPROVED_FOR_MARKETING",
    "WITHHELD",
    "UNKNOWN",
]
StudyType = Literal["INTERVENTIONAL", "OBSERVATIONAL", "EXPANDED_ACCESS"]
SponsorClass = Literal["NIH", "FED", "OTHER_GOV", "INDIV", "INDUSTRY", "NETWORK", "AMBIG", "OTHER", "UNKNOWN"]
InterventionType = Literal[
    "BEHAVIORAL",
    "BIOLOGICAL",
    "COMBINATION_PRODUCT",
    "DEVICE",
    "DIAGNOSTIC_TEST",
    "DIETARY_SUPPLEMENT",
    "DRUG",
    "GENETIC",
    "PROCEDURE",
    "RADIATION",
    "OTHER",
]
Sex = Literal["FEMALE", "MALE", "ALL"]
AgeGroup = Literal["CHILD", "ADULT", "OLDER_ADULT"]
Allocation = Literal["RANDOMIZED", "NON_RANDOMIZED", "NA"]
Masking = Literal["NONE", "SINGLE", "DOUBLE", "TRIPLE", "QUADRUPLE"]
PrimaryPurpose = Literal[
    "TREATMENT",
    "PREVENTION",
    "DIAGNOSTIC",
    "ECT",
    "SUPPORTIVE_CARE",
    "SCREENING",
    "HEALTH_SERVICES_RESEARCH",
    "BASIC_SCIENCE",
    "DEVICE_FEASIBILITY",
    "OTHER",
]
ResultsPosted = Literal["true", "false"]
InterventionModel = Literal["PARALLEL", "CROSSOVER", "FACTORIAL", "SEQUENTIAL", "SINGLE_GROUP"]
DateField = Literal["start_date", "primary_completion_date", "completion_date", "first_posted_date"]
ClosedDimension = Literal[
    "phase",
    "overall_status",
    "study_type",
    "sponsor_class",
    "intervention_type",
    "sex",
    "age_group",
    "allocation",
    "masking",
    "primary_purpose",
    "has_results",
    "intervention_model",
]
DimensionKey = Literal[
    "phase",
    "overall_status",
    "study_type",
    "sponsor_class",
    "intervention_type",
    "sex",
    "age_group",
    "allocation",
    "masking",
    "primary_purpose",
    "has_results",
    "intervention_model",  # closed
    "country",
    "state",
    "sponsor",
    "drug",
    "condition",  # open
    "start_date",
    "primary_completion_date",
    "completion_date",
    "first_posted_date",  # dates
    "enrollment",  # number, binned
]
NumericField = Literal["enrollment", "duration_months", "site_count"]
NodeKind = Literal["sponsor", "drug", "condition", "country"]
SortField = Literal["enrollment", "start_date", "completion_date", "first_posted_date"]
FilterFamily = Literal[
    "phases",
    "statuses",
    "exclude_statuses",
    "study_types",
    "sponsor_classes",
    "intervention_types",
    "sexes",
    "age_groups",
    "allocations",
    "maskings",
    "primary_purposes",
    "has_results",
    "intervention_models",
]
Statistic = Literal["median", "mean", "sum"]
# The request field and the catalogue dimension of every filter family that includes values.
FAMILY_FIELDS: Final[dict[FilterFamily, str]] = {
    "phases": "trial_phase",
    "statuses": "status",
    "study_types": "study_type",
    "sponsor_classes": "sponsor_class",
    "intervention_types": "intervention_type",
    "sexes": "sex",
    "age_groups": "age_group",
    "allocations": "allocation",
    "maskings": "masking",
    "primary_purposes": "primary_purpose",
    "has_results": "has_results",
    "intervention_models": "intervention_model",
}
FAMILY_DIMENSIONS: Final[dict[FilterFamily, str]] = {
    "phases": "phase",
    "statuses": "overall_status",
    "study_types": "study_type",
    "sponsor_classes": "sponsor_class",
    "intervention_types": "intervention_type",
    "sexes": "sex",
    "age_groups": "age_group",
    "allocations": "allocation",
    "maskings": "masking",
    "primary_purposes": "primary_purpose",
    "has_results": "has_results",
    "intervention_models": "intervention_model",
}
ChartType = Literal[
    "bar_chart", "time_series", "histogram", "scatter_plot", "network_graph", "table", "metric"
]

# Not in the model's schema as names: each is a plain `Literal` there, exactly as written in the plan.
TimeUnit = Literal["year", "quarter", "month"]
Pairing = Literal["same_trial", "same_arm"]
SortOrder = Literal["desc", "asc"]


class Entity(PlanModel):
    kind: EntityKind
    value: str = Field(
        description="The words exactly as they appear in the question or in a structured field. "
        "Never translate, expand, correct or add a name."
    )
    role: Literal["filter", "compare", "exclude"] = Field(
        description="'filter': every counted trial must match. "
        "'compare': one side of an 'A vs B' comparison. "
        "'exclude': no counted trial may match ('excluding', 'without', 'not involving')."
    )


class FilterEvidence(PlanModel):
    family: FilterFamily
    phrase: str = Field(description="The words of the question that state this filter, copied verbatim.")


class PlanFilters(PlanModel):
    phases: list[Phase] = Field(description="Empty unless the question restricts phase.")
    statuses: list[Status] = Field(
        description="Empty unless the question restricts status. 'recruiting' means RECRUITING only."
    )
    exclude_statuses: list[Status] = Field(
        description="Empty unless the question leaves statuses out, e.g. 'exclude terminated studies'."
    )
    study_types: list[StudyType] = Field(description="Empty unless the question restricts study type.")
    sponsor_classes: list[SponsorClass] = Field(
        description="Empty unless the question restricts the kind of sponsor, e.g. industry."
    )
    intervention_types: list[InterventionType] = Field(
        description="Empty unless the question restricts the kind of intervention."
    )
    sexes: list[Sex] = Field(description="Empty unless the question restricts the sexes eligible.")
    age_groups: list[AgeGroup] = Field(description="Empty unless the question restricts the age group.")
    allocations: list[Allocation] = Field(
        description="Empty unless the question restricts allocation: 'randomized' is RANDOMIZED."
    )
    maskings: list[Masking] = Field(
        description="Empty unless the question restricts masking, e.g. 'double-blind' is DOUBLE."
    )
    primary_purposes: list[PrimaryPurpose] = Field(
        description="Empty unless the question restricts the primary purpose, e.g. 'prevention trials'."
    )
    has_results: list[ResultsPosted] = Field(
        description="Empty unless the question restricts whether results are posted: ['true'] or ['false']."
    )
    intervention_models: list[InterventionModel] = Field(
        description="Empty unless the question restricts the assignment model, e.g. 'crossover'."
    )
    evidence: list[FilterEvidence] = Field(description="One item for every non-empty list above.")
    date_field: DateField | None = Field(
        description="Which date year_from and year_to apply to; null lets the service decide."
    )
    year_from: int | None = Field(
        ge=1900, le=2100, description="First year, inclusive; null if the question gives none."
    )
    year_to: int | None = Field(
        ge=1900, le=2100, description="Last year, inclusive; null if the question gives none."
    )


# Count trials by one dimension, optionally split by a second.
class Aggregate(PlanModel):
    kind: Literal["aggregate"]
    dimension: DimensionKey = Field(
        description="What trials are counted by: a category, a date or enrollment size."
    )
    series: ClosedDimension | None = Field(
        description="A second, closed dimension that splits the first; null otherwise."
    )
    time_unit: TimeUnit | None = Field(
        description="Only for date dimensions; null lets the service use year."
    )
    top_n: int | None = Field(
        ge=1, le=500, description="Only when the user asks for a number of items; otherwise null."
    )
    statistic: Statistic | None = Field(
        description="Null counts trials. 'median', 'mean' or 'sum' of the numeric field `of` instead."
    )
    of: NumericField | None = Field(description="The numeric field of a statistic; null when counting.")


# One number.
class Total(PlanModel):
    kind: Literal["total"]
    statistic: Statistic | None = Field(
        description="Null counts trials. 'median', 'mean' or 'sum' of the numeric field `of` instead."
    )
    of: NumericField | None = Field(description="The numeric field of a statistic; null when counting.")


# One point per trial.
class Relate(PlanModel):
    kind: Literal["relate"]
    x: NumericField
    y: NumericField
    color_by: ClosedDimension | None


# Co-occurrence of two kinds of thing across trials.
class Network(PlanModel):
    kind: Literal["network"]
    source: NodeKind
    target: NodeKind = Field(description="The same kind as source for co-occurrence, e.g. drug and drug.")
    link: Pairing | None = Field(
        description="'same_arm' when the question is about combinations; null lets the service decide."
    )


# The largest, latest or earliest N trials.
class TrialList(PlanModel):
    kind: Literal["trial_list"]
    sort_by: SortField
    order: SortOrder
    limit: int | None = Field(ge=1, le=500, description="Only when the user gives a number; otherwise null.")


class Clarify(PlanModel):
    kind: Literal["clarify"]
    reason: Literal["missing_entity", "ambiguous_request"]
    missing: list[Literal["drug_name", "condition", "sponsor", "country", "compare", "group_by"]]


class Unsupported(PlanModel):
    kind: Literal["unsupported"]
    category: Literal[
        "not_about_clinical_trials",
        "needs_data_not_in_registry",
        "single_trial_lookup",
        "analysis_not_supported",
        "other",
    ]
    reason: str = Field(description="One sentence. No figures other than those in the question.")


# The message is conversation, not a question about trials; code writes every word of the reply.
class Converse(PlanModel):
    kind: Literal["converse"]
    topic: Literal["greeting", "thanks", "capabilities", "small_talk"]


Analysis = Aggregate | Total | Relate | Network | TrialList | Clarify | Unsupported | Converse


class QueryPlan(PlanModel):
    interpretation: str = Field(
        description="One sentence restating what will be counted and how it is grouped. "
        "No figures other than those in the question."
    )
    entities: list[Entity] = Field(
        max_length=12, description="Named drugs, conditions, sponsors, countries or other terms."
    )
    filters: PlanFilters
    analysis: Analysis
    chart_preference: ChartType | None = Field(
        description="Only when the user names a chart form; otherwise null."
    )
    unapplied: list[str] = Field(
        max_length=5,
        description="Words copied from the question that ask to make part of the chart look different "
        "(highlight, mark, annotate, colour); empty otherwise, and empty for most questions. Never a "
        "counting rule or a wish for a clear chart, and never a reason for 'unsupported'.",
    )


@dataclass(frozen=True)
class PlanIssue:
    """A problem in a plan that code cannot repair, sent back to the model for its one repair turn."""

    code: str
    path: str
    message: str
    allowed: tuple[str, ...] = ()  # the values that would be accepted at `path`, when there is a closed list
