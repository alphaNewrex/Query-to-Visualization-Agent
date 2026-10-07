"""The requests: `QueryRequest` for `POST /v1/query` and `AnalysisRequest` for `POST /v1/analyses`."""

import re
import unicodedata
from typing import Annotated, Final, Literal, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from ctviz.contract.plan import (
    AgeGroup,
    Allocation,
    ChartType,
    ClosedDimension,
    DateField,
    DimensionKey,
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

_WHITESPACE: Final = re.compile(r"\s+")
_CLOSED_DIMENSIONS: Final = get_args(ClosedDimension)

# "Phase 2", "phase2", "2" and "II" are one phase; "Phase 2/3" and "Phase II/III" are two.
_PHASE_NUMBER: Final = r"(?:[1-4]|iv|iii|ii|i)"
_PHASE: Final = re.compile(
    rf"(?P<early>early[ _-]*)?(?:phase[ _-]*)?(?P<first>{_PHASE_NUMBER})"
    rf"(?:\s*/\s*(?:phase[ _-]*)?(?P<second>{_PHASE_NUMBER}))?",
    re.IGNORECASE,
)
_ARABIC_OF_ROMAN: Final = {"i": "1", "ii": "2", "iii": "3", "iv": "4"}
_NOT_APPLICABLE: Final = frozenset({"na", "n/a", "not applicable", "not_applicable"})

_Word = Annotated[str, Field(min_length=1, max_length=200)]
_Words = Annotated[list[_Word], Field(max_length=5)]

_ENTITY_FIELDS: Final = ("drug_name", "condition", "sponsor", "country", "term")
_ENUM_FIELDS: Final = (
    "status",
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
)


def _arabic(numeral: str | None) -> str | None:
    return None if numeral is None else _ARABIC_OF_ROMAN.get(numeral.lower(), numeral)


def phase_tokens(spelling: str) -> list[str] | None:
    """The registry phase tokens a lenient spelling stands for, or None when it is not one.

    A spelling that is not recognised is left to the `Phase` type, whose error lists the tokens.
    """
    text = spelling.strip()
    if text.casefold() in _NOT_APPLICABLE:
        return ["NA"]
    match = _PHASE.fullmatch(text)
    if match is None:
        return None
    first, second = _arabic(match["first"]), _arabic(match["second"])
    if match["early"]:
        return ["EARLY_PHASE1"] if (first, second) == ("1", None) else None
    return [f"PHASE{first}"] if second is None else [f"PHASE{first}", f"PHASE{second}"]


def _empty_is_no_filter(value: object) -> object:
    """An empty array is the same as null: it is how `meta.filters` writes an unset list field."""
    return None if value == [] else value


class CompareSpec(BaseModel):
    """Values of one entity kind to compare side by side, one series each."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    field: Literal["drug_name", "condition", "sponsor", "country"]
    values: Annotated[list[_Word], Field(min_length=2, max_length=5)]


class ExcludeSpec(BaseModel):
    """Trials to leave out: those that match any of these entities or have any of these statuses."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    drug_name: _Words | None = Field(
        None, description="Drugs to leave out; same searches as the filter fields."
    )
    condition: _Words | None = Field(None, description="Conditions to leave out.")
    sponsor: _Words | None = Field(None, description="Lead sponsors to leave out.")
    country: _Words | None = Field(None, description="Countries to leave out (any site there).")
    term: _Words | None = Field(None, description="Other search words to leave out.")
    status: list[Status] | None = Field(None, description="Overall statuses to leave out.")

    @field_validator("drug_name", "condition", "sponsor", "country", "term", "status", mode="before")
    @classmethod
    def _words(cls, value: object) -> object:
        return _empty_is_no_filter([value] if isinstance(value, str) else value)

    @property
    def is_empty(self) -> bool:
        return not any((self.drug_name, self.condition, self.sponsor, self.country, self.term, self.status))


class RequestOptions(BaseModel):
    """Behaviour switches of one request; `meta.options` echoes the effective values."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    planner: Literal["llm", "structured"] = Field(
        "llm",
        description="'llm': the model writes the plan (503 `planner_unavailable` when no model is "
        "configured). 'structured': never call a model; the plan is built from the request fields and "
        "`group_by` is required. The question text is then not interpreted, and the response says so. "
        "Ignored by `POST /v1/analyses`.",
    )
    citations_per_datum: int = Field(
        5,
        ge=0,
        le=100,
        description="Trials cited for each datum; 0 disables citations. A larger value is a bigger "
        "page for the sample call each cell already makes: the answer grows, the request count does not.",
    )
    drug_match: Literal["broad", "name_only"] = Field(
        "broad",
        description="'broad': the registry's intervention search (names, other names, titles, "
        "descriptions, synonyms). 'name_only': intervention names and their synonyms only. The two give "
        "different counts, so the definition used is always stated in the response.",
    )
    include_trace: bool = Field(True, description="Include the step list in `meta.debug.trace`.")
    use_cache: bool = Field(
        True,
        description="false bypasses the plan cache and the response cache for this request; the "
        "registry-call cache stays.",
    )


class PreviousTurn(BaseModel):
    """The previous turn of a conversation: what was asked and the plan that answered it.

    The service keeps no session. The client sends back the question and `meta.plan` of the answer it
    shows, and the planner reads the new message as a follow-up to them.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    query: str | None = Field(
        description="The previous question as sent (`meta.query`); null when the previous turn had none.",
        max_length=1000,
    )
    plan: QueryPlan = Field(description="`meta.plan` of the previous answer.")


class QueryRequest(BaseModel):
    """A question about clinical trials, with optional structured fields.

    Strings are trimmed and unknown keys are rejected. Rules across fields:

    1. Structured filter fields are ANDed with what the question says. Where they conflict, the field
       wins: it replaces the planner's value for the same entity kind or filter family, and the
       override is recorded in `meta.interpretation.adjustments`.
    2. A structured entity field whose value is one of the compared values does not become an extra
       filter; the comparison is kept.
    3. Entity arrays mean "all of"; enum arrays mean "any of". A comparison is always explicit:
       `compare` in the request or an "A vs B" in the question.
    4. `group_by`, `time_unit`, `top_n` and `chart_type` replace the planner's choices.
    5. Every structured field is shown to the model, so "this drug" in the question resolves to
       `drug_name`.
    6. With `previous`, the message may be a follow-up: the planner edits the previous plan, or ignores it
       when the message is unrelated. A name, year or filter may then come from the message or be carried
       over unchanged from the previous plan; one that is in neither is rejected.
    7. `meta.filters` in every response repeats the filter fields in canonical form (arrays for list
       fields, every key present), so it can be sent back as request fields. For that reason every list
       field accepts an empty array as "no filter", the same as null.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        json_schema_extra={
            "examples": [
                {
                    "query": "How has the number of trials for this drug changed over time?",
                    "drug_name": "Pembrolizumab",
                }
            ]
        },
    )

    query: str = Field(
        min_length=1,
        max_length=1000,
        description="The natural-language question. Tabs and line breaks become spaces and runs of "
        "whitespace collapse before validation, because a pasted question can carry line breaks. "
        "Then 1 to 1,000 characters, at least one letter and no other control characters.",
    )
    drug_name: _Words | None = Field(
        None,
        description="Drugs the trials must test, up to 5, each 1 to 200 characters; a single string is "
        "accepted. A trial must match all of them. Searched with the registry's intervention search, so "
        "brand and code names resolve (Keytruda and MK-3475 return the same trials as pembrolizumab).",
    )
    condition: _Words | None = Field(
        None,
        description="Conditions the trials must study; same rules as `drug_name`, searched with the "
        "registry's condition search.",
    )
    sponsor: _Words | None = Field(
        None,
        description="Lead-sponsor names; same rules as `drug_name`, searched in the lead sponsor name.",
    )
    country: _Words | None = Field(
        None,
        description="Countries; same rules as `drug_name`. Each must resolve to one of the registry's "
        "country names (common aliases and ISO alpha-2 or alpha-3 codes are accepted), otherwise the "
        "request is refused with close matches. A trial must list a site in every named country.",
    )
    term: _Words | None = Field(
        None, description="Other search words, matched anywhere in the record; same rules as `drug_name`."
    )
    trial_phase: list[Phase] | None = Field(
        None,
        description="Phases, any of. Lenient spellings are normalised before validation: 'Phase 2', "
        "'phase2', '2' and 'II' become PHASE2, and 'Phase 2/3' becomes both PHASE2 and PHASE3; a single "
        "value is accepted. 'Lists this phase' semantics, as on the registry's website: PHASE2 also "
        "matches Phase 1/Phase 2 and Phase 2/Phase 3 trials.",
    )
    status: list[Status] | None = Field(None, description="Study-level overall statuses, any of.")
    study_type: list[StudyType] | None = Field(None, description="Study types, any of.")
    sponsor_class: list[SponsorClass] | None = Field(None, description="Classes of the lead sponsor, any of.")
    intervention_type: list[InterventionType] | None = Field(
        None, description="Trials with at least one intervention of these types, any of."
    )
    sex: list[Sex] | None = Field(None, description="Sexes the trials accept, any of.")
    age_group: list[AgeGroup] | None = Field(None, description="Age groups the trials include, any of.")
    allocation: list[Allocation] | None = Field(
        None, description="Allocation of interventional trials, any of (RANDOMIZED, NON_RANDOMIZED, NA)."
    )
    masking: list[Masking] | None = Field(None, description="Masking of interventional trials, any of.")
    primary_purpose: list[PrimaryPurpose] | None = Field(None, description="Primary purposes, any of.")
    has_results: list[ResultsPosted] | None = Field(
        None, description="'true': results posted; 'false': none posted."
    )
    intervention_model: list[InterventionModel] | None = Field(
        None, description="Assignment models of interventional trials, any of."
    )
    start_year: int | None = Field(
        None,
        ge=1900,
        le=2100,
        description="First year, inclusive, of the date named by `date_field`. Not after `end_year`.",
    )
    end_year: int | None = Field(
        None,
        ge=1900,
        le=2100,
        description="Last year, inclusive, of the date named by `date_field`. Not before `start_year`.",
    )
    date_field: DateField | None = Field(
        None,
        description="Which date the year bounds apply to. Null means the date on the time axis when "
        "the chart has one, otherwise the study start date.",
    )
    compare: CompareSpec | None = Field(
        None, description="Compare these as series. Replaces any comparison found in the question."
    )
    exclude: ExcludeSpec | None = Field(
        None,
        description="Leave out trials that match any listed entity or have any listed status ('excluding "
        "diabetes', 'without terminated studies'). Replaces what the planner read for the same entity "
        "kind or for statuses; an empty object is no exclusion.",
    )
    group_by: Annotated[list[DimensionKey], Field(min_length=1, max_length=2)] | None = Field(
        None,
        description="Analysis hint: the first key is the axis, the second the series. The second key "
        f"must be one of the closed dimensions ({', '.join(_CLOSED_DIMENSIONS)}). Overrides what the "
        "planner chose. Required when `options.planner` is 'structured'.",
    )
    time_unit: TimeUnit | None = Field(None, description="Analysis hint: bucket width for a date dimension.")
    top_n: int | None = Field(
        None,
        ge=1,
        le=50,
        description="How many categories or rows to keep. Default 15, or 10 for the rows of a trial "
        "list. Network sizes are fixed in v1.",
    )
    chart_type: ChartType | None = Field(
        None,
        description="A preference. Honoured when valid for the data shape, otherwise ignored with a warning.",
    )
    previous: PreviousTurn | None = Field(
        None,
        description="The previous turn, for a follow-up such as 'now split that by phase'. Null (the "
        "default) is a new conversation. Part of the plan cache key.",
    )
    options: RequestOptions = Field(default_factory=RequestOptions)

    @field_validator("query", mode="before")
    @classmethod
    def _collapse_whitespace(cls, value: object) -> object:
        return _WHITESPACE.sub(" ", value) if isinstance(value, str) else value

    @field_validator("query")
    @classmethod
    def _check_text(cls, value: str) -> str:
        if not any(character.isalpha() for character in value):
            raise ValueError("The question must contain at least one letter.")
        if any(unicodedata.category(character) == "Cc" for character in value):
            raise ValueError("The question must not contain control characters.")
        return value

    @field_validator(*_ENTITY_FIELDS, mode="before")
    @classmethod
    def _entity_words(cls, value: object) -> object:
        return _empty_is_no_filter([value] if isinstance(value, str) else value)

    @field_validator("trial_phase", mode="before")
    @classmethod
    def _phase_spellings(cls, value: object) -> object:
        spellings = [value] if isinstance(value, str) else value
        if not isinstance(spellings, list):
            return value
        tokens: list[object] = []
        for spelling in spellings:
            found = phase_tokens(spelling) if isinstance(spelling, str) else None
            tokens.extend(found if found is not None else [spelling])
        return _empty_is_no_filter(list(dict.fromkeys(tokens)))

    @field_validator(*_ENUM_FIELDS, mode="before")
    @classmethod
    def _enum_values(cls, value: object) -> object:
        return _empty_is_no_filter(value)

    @field_validator("end_year")
    @classmethod
    def _years_in_order(cls, end_year: int | None, info: ValidationInfo) -> int | None:
        start_year = info.data.get("start_year")
        if end_year is not None and start_year is not None and start_year > end_year:
            raise ValueError("start_year must not be after end_year.")
        return end_year

    @field_validator("group_by")
    @classmethod
    def _series_is_closed(cls, keys: list[DimensionKey] | None) -> list[DimensionKey] | None:
        if keys is not None and len(keys) == 2 and keys[1] not in _CLOSED_DIMENSIONS:
            raise ValueError(
                f"The second key is the series and must be one of: {', '.join(_CLOSED_DIMENSIONS)}."
            )
        return keys

    @model_validator(mode="after")
    def _structured_mode_needs_group_by(self) -> Self:
        if self.options.planner == "structured" and self.group_by is None:
            raise ValueError("group_by is required when options.planner is 'structured'.")
        return self


class AnalysisRequest(BaseModel):
    """A typed plan to answer without a model: the body of `POST /v1/analyses`.

    Nothing is checked against a question; the plan is validated for shape and limits only. Posting
    `{"plan": meta.plan, "options": meta.options}` from an earlier response reproduces its visualization
    for the same data timestamp.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    plan: QueryPlan
    options: RequestOptions = Field(default_factory=RequestOptions)
