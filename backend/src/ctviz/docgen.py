"""Documentation generated from the code, so that it cannot drift from it.

* `docs/schema/*.json`: the JSON Schema of the contract. One wrapper model, `Contract`, holds every
  top-level type, so a model that a request and a response share is emitted once and a type generator
  can read the whole contract from one file. The schema is exported in serialization mode, in which the
  response models list every key as required (the request models that responses embed keep theirs).
  The output is deterministic and not key-sorted: Pydantic sorts `$defs` and schema keywords by name and
  keeps `properties` in declaration order, so a visualization reads `type`, `title`, `encoding`, `data`.
* `docs/SCHEMA.md`: a field-by-field reference read from that schema and the models' own descriptions.
* `docs/examples/README.md`: an index read from the recorded runs.
* The blocks of the README between `<!-- gen:NAME:start -->` and `<!-- gen:NAME:end -->`.

`generated_documents` computes every file in memory; `stale_documents` compares them with the disk and
`write_documents` writes them. `scripts/gen_docs.py` is the command line, and a test fails when any
committed document is stale.
"""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, cast

from pydantic import BaseModel, TypeAdapter
from pydantic.json_schema import GenerateJsonSchema

from ctviz import examples
from ctviz.capabilities import capabilities
from ctviz.catalog.fields import CATALOG
from ctviz.contract.plan import QueryPlan
from ctviz.contract.request import AnalysisRequest, QueryRequest
from ctviz.contract.response import ErrorResponse, QueryResponse
from ctviz.errors import HTTP_MAPPING, ErrorCode
from ctviz.settings import REPOSITORY_ROOT, Settings

type Schema = dict[str, Any]

SCHEMA_DIRECTORY: Final = REPOSITORY_ROOT / "docs" / "schema"
OPENAPI_FILE: Final = "openapi.json"
SCHEMA_PAGE: Final = "docs/SCHEMA.md"


class DocumentError(Exception):
    """A generated document cannot be written: a marker the README needs is missing."""


# --- The JSON Schema files -----------------------------------------------------------------------------


class NoFieldTitles(GenerateJsonSchema):
    """Leave out the `title` Pydantic adds to every property.

    With them, a type generator turns each property into a separate named alias.
    """

    def field_title_should_be_set(self, schema: object) -> bool:
        return False


class Contract(BaseModel):
    """Every top-level type of the contract, one field each."""

    query_request: QueryRequest
    analysis_request: AnalysisRequest
    query_plan: QueryPlan
    query_response: QueryResponse
    error_response: ErrorResponse


# File name to the type it documents. The combined file comes first.
_SCHEMA_FILES: Final[Mapping[str, Any]] = {
    "contract.v1.schema.json": Contract,
    "query-request.v1.schema.json": QueryRequest,
    "query-plan.v1.schema.json": QueryPlan,
    "query-response.v1.schema.json": QueryResponse,
    "error-response.v1.schema.json": ErrorResponse,
}


def json_schema(documented: Any) -> Schema:
    """The JSON Schema of a model or type alias, in serialization mode."""
    return TypeAdapter(documented).json_schema(mode="serialization", schema_generator=NoFieldTitles)


def openapi_document() -> Schema:
    """The application's OpenAPI document, built from the default settings.

    Taken from `Settings.model_construct()` and not from `Settings()`, so that no environment
    variable and no env file (the real one holds a key) can change the output.
    """
    # Imported here: the contract schemas must not need the web framework.
    from ctviz.api.app import create_app

    return create_app(Settings.model_construct()).openapi()


def contract_documents() -> dict[str, str]:
    """The JSON Schema files, which depend on the contract models alone, as file name to text."""
    return {name: _text(json_schema(documented)) for name, documented in _SCHEMA_FILES.items()}


def schema_documents() -> dict[str, str]:
    """Every generated schema file as file name to text: the contract's schemas, then the OpenAPI document."""
    return {**contract_documents(), OPENAPI_FILE: _text(openapi_document())}


def write_schema_files(directory: Path = SCHEMA_DIRECTORY) -> list[Path]:
    """Write every generated schema file into `directory`, creating it if needed; return the paths."""
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, text in schema_documents().items():
        path = directory / name
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def stale_schema_files(directory: Path = SCHEMA_DIRECTORY) -> list[str]:
    """The names of schema files that are missing from `directory` or differ from a fresh export."""
    return [
        name
        for name, text in schema_documents().items()
        if not (directory / name).is_file() or (directory / name).read_text(encoding="utf-8") != text
    ]


def _text(document: Schema) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


# --- Reading a schema as Markdown ------------------------------------------------------------------------


@dataclass(frozen=True)
class Style:
    """How a table is written: the page its links go to, and how many values of an enum it lists in full."""

    page: str = ""  # empty: the links stay on the page they are in
    preview: int | None = None  # an enum with more values shows its first three and the count


DEFAULT_STYLE: Final = Style()


def _ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[1]


def _link(name: str, page: str = "") -> str:
    """A link to the heading of a definition, on `page` (empty: the same page)."""
    return f"[`{name}`]({page}#{name.lower()})"


def _cell(text: str) -> str:
    """Text for a table cell: one line, with the pipes escaped."""
    return " ".join(text.split()).replace("|", "\\|")


def _literal(value: object) -> str:
    return value if isinstance(value, str) else json.dumps(value)


def _type(schema: Schema, style: Style = DEFAULT_STYLE) -> str:
    """The type of a property as a short Markdown phrase; a reference becomes a link to its heading."""
    if "$ref" in schema:
        return _link(_ref_name(schema["$ref"]), style.page)
    if "const" in schema:
        return f"`{_literal(schema['const'])}`"
    if "enum" in schema:
        values = [f"`{_literal(value)}`" for value in schema["enum"]]
        if style.preview is not None and len(values) > style.preview:
            values = [*values[:3], f"... {len(values)} values"]
        return " \\| ".join(values)
    for key in ("anyOf", "oneOf"):
        if key in schema:
            return " \\| ".join(_type(branch, style) for branch in schema[key])
    kind = schema.get("type", "any")
    if kind == "array":
        item = _type(schema.get("items", {}), style)
        return f"({item})[]" if "\\|" in item else f"{item}[]"
    if kind == "object":
        extra = schema.get("additionalProperties")
        return f"map of {_type(extra, style)}" if isinstance(extra, dict) and extra else "object"
    if kind == "string" and schema.get("format") == "date-time":
        return "string (date-time)"
    return str(kind)


def _range(low: object, high: object, unit: str) -> str:
    """'3 to 1,000 characters', 'at most 5 items', 'exactly 1 item'."""

    def number(value: object) -> str:
        return f"{value:,}" if isinstance(value, int) and unit else str(value)

    def noun(count: object) -> str:
        return unit[:-1] if unit.endswith("s") and count == 1 else unit

    if low is not None and low == high:
        return f"exactly {number(low)} {noun(low)}".strip()
    if low is not None and high is not None:
        return f"{number(low)} to {number(high)} {noun(high)}".strip()
    if low is not None:
        return f"at least {number(low)} {noun(low)}".strip()
    return f"at most {number(high)} {noun(high)}".strip()


def _bounds(node: Schema) -> list[str]:
    """The limits one schema node states itself."""
    found = []
    for low, high, unit in (
        ("minLength", "maxLength", "characters"),
        ("minimum", "maximum", ""),
        ("minItems", "maxItems", "items"),
    ):
        if low in node or high in node:
            found.append(_range(node.get(low), node.get(high), unit))
    if "exclusiveMinimum" in node:
        found.append(f"above {node['exclusiveMinimum']}")
    if "pattern" in node:
        found.append(f"matches `{node['pattern']}`")
    return found


def _constraints(schema: Schema) -> str:
    """The limits of a property, read inside `anyOf` branches and array items too."""
    found: list[str] = []
    for branch in [schema, *schema.get("anyOf", [])]:
        found.extend(_bounds(branch))
        if "items" in branch:
            found.extend(f"each {limit}" for limit in _bounds(branch["items"]))
    return "; ".join(dict.fromkeys(found))


def _first_sentence(text: str) -> str:
    flat = " ".join(text.split())
    end = flat.find(". ")
    return flat if end < 0 else flat[: end + 1]


def _described(prop: Schema, definitions: Mapping[str, Schema]) -> str:
    """The property's own description; a bare reference falls back to its target's first sentence."""
    if "description" in prop:
        return _cell(prop["description"])
    targets = [prop] if "$ref" in prop else [b for b in prop.get("anyOf", []) if "$ref" in b]
    if len(targets) == 1:
        described = definitions.get(_ref_name(targets[0]["$ref"]), {}).get("description")
        return _cell(_first_sentence(described)) if described else ""
    return ""


def request_side(definitions: Mapping[str, Schema]) -> frozenset[str]:
    """The definitions a client can send: everything reachable from the two request bodies."""
    reached: set[str] = set()
    pending = ["QueryRequest", "AnalysisRequest"]
    while pending:
        name = pending.pop()
        if name not in reached:
            reached.add(name)
            pending.extend(_ref_name(ref) for ref in _refs(definitions[name]))
    return frozenset(reached)


def _refs(node: object) -> list[str]:
    if isinstance(node, dict):
        own = [node["$ref"]] if "$ref" in node else []
        return own + [ref for value in node.values() for ref in _refs(value)]
    if isinstance(node, list):
        return [ref for item in node for ref in _refs(item)]
    return []


def _union_table(definition: Schema, definitions: Mapping[str, Schema], style: Style) -> list[str]:
    key = definition["discriminator"]["propertyName"]
    values: dict[str, list[str]] = {}
    for value, ref in definition["discriminator"]["mapping"].items():
        values.setdefault(_ref_name(ref), []).append(value)
    lines = [
        f"One of these shapes, chosen by `{key}`:",
        "",
        f"| `{key}` | Shape | Description |",
        "| --- | --- | --- |",
    ]
    for branch in definition["oneOf"]:
        name = _ref_name(branch["$ref"])
        shown = ", ".join(f"`{value}`" for value in values.get(name, []))
        summary = _cell(_first_sentence(definitions[name].get("description", "")))
        lines.append(f"| {shown} | {_link(name, style.page)} | {summary} |")
    return lines


def _property_table(
    definition: Schema, definitions: Mapping[str, Schema], *, can_be_sent: bool, style: Style
) -> list[str]:
    required = set(definition.get("required", []))
    if can_be_sent:
        lines = [
            "| Field | Type | Required | Default | Constraints | Description |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    else:
        lines = ["| Field | Type | Description |", "| --- | --- | --- |"]
    for field, prop in definition.get("properties", {}).items():
        type_, described = _type(prop, style), _described(prop, definitions)
        if can_be_sent:
            if "default" in prop:
                default = f"`{_literal(prop['default'])}`"
            else:
                default = "" if field in required or "$ref" not in prop else "*(see type)*"
            cells = [type_, "yes" if field in required else "no", default, _constraints(prop), described]
        else:
            cells = [type_, described]
        lines.append(f"| `{field}` | " + " | ".join(cells) + " |")
    extra = definition.get("additionalProperties")
    if isinstance(extra, dict):
        cells = [_type(extra, style), "no", "", "", "A data field named by the encoding."]
        lines.append(
            "| *(any other key)* | " + " | ".join(cells if can_be_sent else [cells[0], cells[-1]]) + " |"
        )
    return lines


def render_definition(
    name: str,
    definitions: Mapping[str, Schema],
    *,
    can_be_sent: bool,
    style: Style = DEFAULT_STYLE,
    level: int = 3,
    with_example: bool = True,
) -> str:
    """The heading, description and field table of one definition."""
    definition = definitions[name]
    lines = [f"{'#' * level} {name}", ""]
    if definition.get("description"):
        lines += [definition["description"].strip(), ""]
    if "oneOf" in definition:
        lines += _union_table(definition, definitions, style)
    else:
        lines += _property_table(definition, definitions, can_be_sent=can_be_sent, style=style)
    if with_example and definition.get("examples"):
        example = json.dumps(definition["examples"][0], indent=2, ensure_ascii=False)
        lines += ["", "Example:", "", "```json", example, "```"]
    return "\n".join(lines) + "\n"


README_STYLE: Final = Style(page=SCHEMA_PAGE, preview=8)


def request_tables(schema: Schema, style: Style = README_STYLE, level: int = 4) -> str:
    """The tables of the README's request section: the body of `POST /v1/query` and the two types it holds."""
    definitions = schema["$defs"]
    return "\n".join(
        render_definition(name, definitions, can_be_sent=True, style=style, level=level, with_example=False)
        for name in ("QueryRequest", "RequestOptions", "CompareSpec")
    )


def _names(names: str) -> tuple[str, ...]:
    return tuple(names.split())


@dataclass(frozen=True)
class Section:
    """A group of definitions in `docs/SCHEMA.md`, with the prose that introduces it."""

    title: str
    intro: str
    definitions: tuple[str, ...]


_ROW_KEY_NAMES: Final = """\
| What | Row keys |
| --- | --- |
| A category dimension | its catalogue key: `phase`, `country`, `sponsor_class` |
| A date dimension | the date's stem and the unit: `start_year`, `completion_month`, `first_posted_quarter` |
| Enrollment bins in a bar chart | `enrollment_bin` |
| A count; a share of the scope's trials | `trial_count`; `share` (a fraction, drawn with the format `.1%`) |
| A statistic of a number | `median_enrollment`, `mean_duration_months`, `sum_site_count` |
| The groups of a comparison | `group` (the channel's `title` says what is compared: "Drug", "Condition") |
| A histogram bin | `bin_start`, `bin_end` (null in an open last bin), `bin_label`, `trial_count` |
| A trial (a scatter point, a table row) | `nct_id`, `title`, `url`, then the columns the encoding names |
| A network node | `id`, `label`, `entity_type`, `trial_count` |
| A network link | `id`, `source`, `target`, `trial_count` |
| A country row, additionally | `iso_alpha3` (a string, or null) |
"""

_CONVENTIONS: Final = """\
- Every key is always present. `null` means not applicable, and an array is never null.
- Data is render-ready: flat rows, one per drawn mark, already aggregated, binned, sorted, zero-filled and
  truncated. A renderer never filters, sorts or computes, and needs nothing from `meta` to draw.
- Keys are `snake_case`. The service's own enumerations are lower-case (`bar_chart`, `time_series`);
  ClinicalTrials.gov tokens (`PHASE3`, `RECRUITING`) appear untranslated only in filters, plans, citation
  excerpts and URLs. Booleans the service computes start with `is_` (`meta.options` echoes the request's
  own names), and timings end with `_ms`.
- Category cells hold display labels ("Phase 1/Phase 2"). Counts are integers. A share is a fraction
  (0.176) drawn with a percent format. A period is a label (`2015`, `2024-Q2`, `2024-06`): treat it as an
  ordered category and never parse it as a date, because a local-time parse can shift a year.
- No colours are sent. Colour index `i` belongs to `domain[i]`.
- Every string is plain text. Sponsor, drug and trial names are third-party text: render them as text,
  never as HTML.
"""

_SECTIONS: Final = (
    Section(
        "Requests",
        "`POST /v1/query` takes a `QueryRequest`; `POST /v1/analyses` takes an `AnalysisRequest`. "
        "Both reject an unknown key with HTTP 422 and an `invalid_request` error body (see "
        "[Error body](#error-body)) that names the key, so a misspelt field is never ignored silently.",
        _names("QueryRequest RequestOptions CompareSpec ExcludeSpec AnalysisRequest"),
    ),
    Section(
        "Query plan",
        "The plan is what the planner model writes, what `POST /v1/analyses` accepts and what every "
        "response echoes in `meta.plan`. It is a closed vocabulary: no field can hold a data value, a row "
        "or an NCT ID. Every field is required, because the schema is written for strict structured "
        "outputs. `analysis` is one of seven shapes, told apart by `kind`; rows without a default below "
        "must be sent.",
        _names(
            "QueryPlan Entity PlanFilters FilterEvidence Aggregate Total Relate Network TrialList "
            "Clarify Unsupported"
        ),
    ),
    Section(
        "Response envelope and non-chart outcomes",
        "Every HTTP 200 body has the same seven keys: `spec_version`, `kind`, `message`, "
        "`visualization`, `clarification`, `references` and `meta`. `kind` says which of three shapes it "
        "is. A finished interpretation is always HTTP 200, also when there is nothing to draw: "
        "`clarification` (the service needs a name or a choice), `unsupported` (outside what the service "
        "answers) and `no_data` (the plan ran and nothing matched) all carry `visualization: null` and "
        "`references: {}`. HTTP errors are for failures of the service or of its dependencies.\n\n"
        "Conventions that hold everywhere in a response:\n\n" + _CONVENTIONS,
        _names(
            "QueryResponse VisualizationResponse ClarificationResponse MessageResponse Clarification "
            "LabeledRequest"
        ),
    ),
    Section(
        "Visualization types",
        "`visualization.type` selects one of seven shapes. Each has `type`, `title`, `subtitle`, "
        "`encoding` and `data`, and the extra keys its table lists. The encoding maps channels to row "
        "keys; `data` holds the rows.",
        _names(
            "Visualization BarChart BarChartEncoding TimeSeries TimeSeriesEncoding Histogram "
            "HistogramEncoding ScatterPlot ScatterPlotEncoding NetworkGraph NetworkEncoding "
            "NetworkNodeEncoding NetworkEdgeEncoding NetworkData Table TableEncoding Metric "
            "MetricEncoding"
        ),
    ),
    Section(
        "Channels",
        "A channel names a row key (`field`) and says how to read it. A category channel lists every value "
        "in `domain`, which is the order of the axis, the legend and the colours.",
        _names("CategoryChannel ChannelSort TemporalChannel QuantitativeChannel FieldDef FieldRef"),
    ),
    Section(
        "Data rows",
        "A row carries the keys its encoding names, beside three reserved keys that cite its evidence. "
        "The names follow rules:\n\n" + _ROW_KEY_NAMES,
        _names("Datum Node Edge"),
    ),
    Section(
        "Citations",
        "Every datum cites up to `meta.citations.max_per_datum` trials. `references` holds exactly the "
        "trials cited, keyed by NCT ID, so a title and a link appear once however many rows cite a trial. "
        "A citation's `excerpt` is the value found at `field` in the record that ClinicalTrials.gov "
        "returned during the request, copied verbatim; `null` means the field is absent and the absence is "
        "the evidence. In this version `scope_evidence` is always an empty array.",
        _names("Citation TrialReference ScopeEvidence"),
    ),
    Section(
        "Metadata",
        "`meta` explains the answer: how the question was read, what was counted and what was left out. "
        "`meta.debug` and the `detail` of a trace step are for people debugging and are outside the "
        "stability promise. `StrategyStep.name` lists `sample_then_recount`, a strategy that this version "
        "never uses.",
        _names(
            "Meta AppliedFilters AppliedExclusions Interpretation EntityResolution RegistryTerm OtherReading "
            "SponsorCandidate Measure StrategyStep PlannerInfo Usage Adjustment Note Source "
            "UpstreamRequest Counts SeriesCounts ExclusionCount Truncation TruncationItem CitationsInfo "
            "CacheInfo Timing Debug TraceStep"
        ),
    ),
    Section(
        "Error body",
        "Every response with a status other than 200, from the backend and from the frontend's proxy, has "
        "this body.",
        _names("ErrorResponse ErrorBody"),
    ),
)

_PREAMBLE: Final = """\
# Schema reference

Generated by `backend/scripts/gen_docs.py` from the Pydantic models in `backend/src/ctviz/contract/`.
Do not edit it by hand: `make docs` rewrites it, and `make check` fails when it is stale. The same models
produce the JSON Schemas in [`docs/schema/`](schema/) (`contract.v1.schema.json` holds every definition
below) and the OpenAPI document. Fields are listed in the order the models declare them.

Everything a client can send has a Required, Default and Constraints column. Everything the service sends
lists every key, so it has no Required column.
"""


def schema_reference(schema: Schema | None = None) -> str:
    """The text of `docs/SCHEMA.md`: every definition of the contract, in sections.

    A definition that no section lists is still documented, under "Other types", and a section lists
    only the names the contract has, so a model added or renamed later changes the grouping and
    never the completeness.
    """
    schema = schema if schema is not None else json_schema(Contract)
    definitions: Mapping[str, Schema] = schema["$defs"]
    placed = {name for section in _SECTIONS for name in section.definitions}
    leftover = tuple(sorted(set(definitions) - placed))
    sections = [
        Section(s.title, s.intro, tuple(n for n in s.definitions if n in definitions)) for s in _SECTIONS
    ]
    if leftover:
        sections.append(
            Section("Other types", "Definitions of the contract that no section above lists.", leftover)
        )
    sendable = request_side(definitions)
    contents = [
        f"- [{section.title}](#{_slug(section.title)}): "
        + ", ".join(_link(name) for name in section.definitions)
        for section in sections
    ]
    parts = [_PREAMBLE, "## Contents\n\n" + "\n".join(contents) + "\n"]
    for section in sections:
        parts.append(f"## {section.title}\n\n{section.intro}\n")
        parts.extend(
            render_definition(name, definitions, can_be_sent=name in sendable) for name in section.definitions
        )
    return "\n".join(parts)


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9 -]", "", title.lower()).replace(" ", "-")


# --- Tables read from the code ---------------------------------------------------------------------------


_ERROR_MEANINGS: Final[Mapping[ErrorCode, str]] = {
    ErrorCode.INVALID_REQUEST: "The body broke a validation rule; `details.errors` lists each path, "
    "code and message.",
    ErrorCode.NOT_FOUND: "No such path, schema or recorded example.",
    ErrorCode.METHOD_NOT_ALLOWED: "The path exists but not for this method.",
    ErrorCode.PLANNER_UNAVAILABLE: "No model could write a plan; `details.reason` is `not_configured`, "
    "`configuration` or `transient`. Only `transient` is retryable. Structured mode and `/v1/analyses` "
    "need no model.",
    ErrorCode.UPSTREAM_UNAVAILABLE: "ClinicalTrials.gov answered 5xx, refused the connection or sent an "
    "unreadable body, after retries.",
    ErrorCode.UPSTREAM_RATE_LIMITED: "ClinicalTrials.gov kept throttling after backoff; "
    "`Retry-After` is set.",
    ErrorCode.UPSTREAM_TIMEOUT: "ClinicalTrials.gov did not answer in time, after retries.",
    ErrorCode.DEADLINE_EXCEEDED: "The request ran past its own deadline ({deadline:g} s by default).",
    ErrorCode.INTERNAL_ERROR: "A bug, or an answer that failed an internal consistency check and was "
    "withheld instead of sent. Quote `request_id`.",
}
_PROXY_ERROR: Final = (
    "backend_unreachable",
    "502",
    "yes",
    "Sent only by the frontend's proxy: the backend did not answer.",
)


def errors_table() -> str:
    """The error codes with their HTTP status and whether repeating the request can help."""
    missing = [code for code in ErrorCode if code not in _ERROR_MEANINGS]
    if missing:
        raise DocumentError(f"Describe these error codes in docgen.py: {missing}")
    deadline = Settings.model_construct().request_deadline_s
    rows = [
        (
            str(code),
            str(HTTP_MAPPING[code].http_status),
            "yes" if HTTP_MAPPING[code].is_retryable else "no",
            meaning.format(deadline=deadline),
        )
        for code, meaning in _ERROR_MEANINGS.items()
    ]
    lines = ["| `error.code` | HTTP status | Retryable | Meaning |", "| --- | --- | --- | --- |"]
    lines += [
        f"| `{code}` | {status} | {retry} | {meaning} |"
        for code, status, retry, meaning in [*rows, _PROXY_ERROR]
    ]
    return "\n".join(lines)


def endpoints_table() -> str:
    """The routes of the API, read from its OpenAPI document."""
    lines = ["| Method and path | What it does |", "| --- | --- |"]
    for path, operations in openapi_document()["paths"].items():
        for method, operation in operations.items():
            lines.append(f"| `{method.upper()} {path}` | {_cell(operation.get('summary', ''))} |")
    return "\n".join(lines)


# What each setting does, by field name. A setting that is not listed here is still in the table.
_SETTING_NOTES: Final[Mapping[str, str]] = {
    "openai_api_key": "The planner's key. Blank, or the placeholder of `.example.env`, counts as no key.",
    "openai_api_base": "Base URL of the OpenAI API; it must offer the Responses API.",
    "allowed_models": "Comma-separated models the key may use. Once a key is set, the planner models must be "
    "in it.",
    "planner_model": "The model that writes plans.",
    "planner_effort": "Reasoning effort sent with the planner model; the families without one get "
    "temperature 0.",
    "planner_fallback_model": "Asked once when the planner model fails.",
    "planner_timeout_s": "Seconds to wait for one model call.",
    "ctgov_base_url": "The ClinicalTrials.gov Data API.",
    "ctgov_concurrency": "Registry requests in flight at once.",
    "ctgov_burst": "Registry requests let through at once.",
    "ctgov_rate_per_s": "Registry requests per second after the burst.",
    "one_page_max": "The most trials one registry request returns (its page-size limit).",
    "walk_cap": "The most trials a paged walk reads.",
    "max_fanout_requests": "The most count requests one question may make.",
    "low_match_threshold": "A name that matches fewer trials gets the `low_match_count` warning.",
    "request_deadline_s": "Seconds before a request is answered 504 `deadline_exceeded`.",
    "cache_ttl_s": "Seconds a registry answer is kept in memory.",
    "plan_cache_size": "Plans kept in memory.",
    "response_cache_size": "Finished responses kept in memory.",
    "examples_dir": "The folder `GET /v1/examples` reads (default `docs/examples`).",
    "log_format": "`console` or `json`.",
}


def _shown_default(value: object) -> str:
    if value is None or value == frozenset():
        return "none"
    if isinstance(value, float) and value.is_integer():
        return f"`{int(value)}`"
    return f"`{value}`"


def settings_table() -> str:
    """Every setting: the variable that sets it, its default and what it does."""
    lines = ["| Variable | Default | What it does |", "| --- | --- | --- |"]
    for name, field in Settings.model_fields.items():
        alias = field.validation_alias
        variable = alias if isinstance(alias, str) else f"CTVIZ_{name.upper()}"
        lines.append(
            f"| `{variable}` | {_shown_default(field.default)} | {_cell(_SETTING_NOTES.get(name, ''))} |"
        )
    return "\n".join(lines)


_VISUALIZATION_KEYS: Final = frozenset({"type", "title", "subtitle", "encoding", "data"})


def response_types_table(schema: Schema, style: Style = README_STYLE) -> str:
    """One row per visualization type: its extra keys, the keys of its encoding and the shape of its data."""
    definitions: Mapping[str, Schema] = schema["$defs"]
    lines = ["| `type` | Extra keys | `encoding` keys | `data` |", "| --- | --- | --- | --- |"]
    for branch in definitions["Visualization"]["oneOf"]:
        name = _ref_name(branch["$ref"])
        definition = definitions[name]
        properties = definition["properties"]
        kind = properties["type"]["const"]
        extras = [
            f"`{key}`: {_type(prop, style)}"
            for key, prop in properties.items()
            if key not in _VISUALIZATION_KEYS
        ]
        encoding = definitions[_ref_name(properties["encoding"]["$ref"])]
        keys = ", ".join(f"`{key}`" for key in encoding["properties"])
        shape = _cell(definition.get("description", ""))
        lines.append(f"| `{kind}` | {'; '.join(extras) or 'none'} | {keys} | {shape} |")
    return "\n".join(lines)


_DIMENSION_KINDS: Final = {
    "category": "closed list",
    "entity": "open list",
    "date": "date",
    "number": "number, binned",
}


def capabilities_tables() -> str:
    """The dimensions a question can group by and the limits, as `GET /v1/capabilities` reports them."""
    document = capabilities(CATALOG, Settings.model_construct())
    lines = [
        "| `group_by` key | Kind | A trial counts under | Values |",
        "| --- | --- | --- | --- |",
    ]
    for dimension in _dicts(document["dimensions"]):
        labels = [str(label) for label in dimension["buckets"]]
        kind = str(dimension["kind"])
        if kind == "date":
            values = "years, quarters or months"
        elif not labels:
            values = "from the data"
        elif len(labels) <= 4:
            values = ", ".join(labels)
        else:
            values = f"{len(labels)} values"
        counted = "one value" if dimension["is_exclusive"] else "one or more values"
        lines.append(f"| `{dimension['key']}` | {_DIMENSION_KINDS[kind]} | {counted} | {values} |")
    limits = cast(Mapping[str, Any], document["limits"])
    lines += [
        "",
        f"Numeric fields (scatter plots and statistics): {_codes(document['numeric_fields'])}. "
        f"Network node kinds: {_codes(document['node_kinds'])}. "
        f"Visualization types: {_codes(document['visualization_types'])}.",
        "",
        f"Limits: a request to ClinicalTrials.gov returns at most {limits['one_page_max']:,} trials; a "
        f"paged walk reads at most {limits['walk_cap']:,}; a count fan-out makes at most "
        f"{limits['max_fanout_requests']} requests; {limits['default_top_n']} categories by default and "
        f"at most {limits['max_top_n']}; at most {limits['max_series']} series; at most "
        f"{limits['max_links']} links in a network; a request is cut off after "
        f"{limits['request_deadline_s']:g} s.",
    ]
    return "\n".join(lines)


def _dicts(value: object) -> list[Mapping[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _codes(values: object) -> str:
    return ", ".join(f"`{value}`" for value in values) if isinstance(values, list) else ""


# --- The recorded examples -------------------------------------------------------------------------------


@dataclass(frozen=True)
class ExampleRun:
    """What the index says about one recorded run, read from its files."""

    slug: str
    question: str
    kind: str
    visualization: str
    variant: str
    headline: str
    trials_matched: str
    model: str
    recorded: str
    data_timestamp: str


def _variant(visualization: Mapping[str, Any]) -> str:
    kind = visualization["type"]
    if kind == "bar_chart":
        if visualization["stack"] == "stacked":
            return "stacked"
        return "grouped" if visualization["encoding"]["series"] else str(visualization["orientation"])
    if kind == "time_series":
        return str(visualization["mark"])
    if kind == "network_graph":
        return str(visualization["layout"])
    return ""


def _question(request: Mapping[str, Any]) -> str:
    fields = [
        f"{key}: {', '.join(value) if isinstance(value, list) else value}"
        for key, value in request.items()
        if key not in ("query", "options")
    ]
    return str(request["query"]) + (f" ({'; '.join(fields)})" if fields else "")


def _matched(counts: Mapping[str, Any] | None) -> str:
    if counts is None:
        return "-"
    series = counts["series"]
    if len(series) == 1:
        return f"{series[0]['trials_matched']:,}"
    return "; ".join(f"{item['label']}: {item['trials_matched']:,}" for item in series)


def read_examples(directory: Path) -> list[ExampleRun]:
    """The recorded runs of `directory`, in order, read from `request.json` and `response.json`."""
    runs = []
    for slug in examples.slugs(directory):
        request = json.loads((directory / slug / "request.json").read_text(encoding="utf-8"))
        response = json.loads((directory / slug / "response.json").read_text(encoding="utf-8"))
        meta, visualization = response["meta"], response["visualization"]
        source = meta["source"]
        runs.append(
            ExampleRun(
                slug=slug,
                question=_question(request),
                kind=response["kind"],
                visualization=visualization["type"] if visualization else "none",
                variant=_variant(visualization) if visualization else "",
                headline=response["message"],
                trials_matched=_matched(meta["counts"]),
                model=meta["planner"]["model"] or "none",
                recorded=meta["generated_at"][:10],
                data_timestamp=source["data_timestamp"] if source else "",
            )
        )
    return runs


FEATURED: Final = (
    "01-assignment-request",
    "02-compare-phases",
    "03-recruiting-by-country",
    "04-sponsor-drug-network",
    "05-drug-cooccurrence",
)
_REPLAY: Final = """\
jq -c '{plan: .meta.plan, options: .meta.options}' docs/examples/01-assignment-request/response.json \\
  | curl -s -X POST http://127.0.0.1:8000/v1/analyses -H 'Content-Type: application/json' -d @-
"""


def _shown(run: ExampleRun) -> str:
    if run.visualization == "none":
        return f"`{run.kind}` (nothing to draw)"
    return f"`{run.visualization}`" + (f" ({run.variant})" if run.variant else "")


def _runs_table(runs: Sequence[ExampleRun], base: str) -> str:
    lines = ["| Run | Question | Visualization | Headline |", "| --- | --- | --- | --- |"]
    for run in runs:
        link = f"[{run.slug}]({base}{run.slug}/response.json)"
        lines.append(f"| {link} | {_cell(run.question)} | {_shown(run)} | {_cell(run.headline)} |")
    return "\n".join(lines)


def examples_tables(directory: Path, base: str) -> str:
    """The README's two tables: the featured runs, then the others. `base` is the path to `directory`."""
    runs = read_examples(directory)
    featured = [run for run in runs if run.slug in FEATURED]
    others = [run for run in runs if run.slug not in FEATURED]
    parts = ["**Featured**", "", _runs_table(featured, base)]
    if others:
        parts += ["", "**Also recorded**", "", _runs_table(others, base)]
    return "\n".join(parts)


def _joined(values: Sequence[str]) -> str:
    return ", ".join(sorted(set(values))) or "-"


def examples_index(directory: Path) -> str:
    """The text of `docs/examples/README.md`, read from the recorded runs themselves."""
    runs = read_examples(directory)
    lines = [
        "# Example runs",
        "",
        "Actual outputs of this service for ten questions, recorded by `make examples` "
        "(`backend/scripts/run_examples.py`). Nothing here was edited by hand. Each folder holds:",
        "",
        "- `request.json`: the body sent to `POST /v1/query`;",
        "- `plan.json`: the plan the service ran, which is `meta.plan` of the response;",
        "- `response.json`: the complete answer, with every citation and the trace.",
        "",
        f"Recorded on {_joined([run.recorded for run in runs])} (UTC) from ClinicalTrials.gov data of "
        f"{_joined([run.data_timestamp for run in runs if run.data_timestamp])}, with the planner model "
        f"{_joined([run.model for run in runs])}. A model's plan is not repeatable run to run, but a "
        "recorded plan is: posting it to `POST /v1/analyses` re-runs the same analysis with no model, and "
        "the counts stay the same until the registry's data version changes. From the repository root, "
        "with the API running on port 8000:",
        "",
        "```bash",
        _REPLAY.rstrip(),
        "```",
        "",
        "| Run | Question | Outcome | Visualization | Headline | Trials matched | Model |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for run in runs:
        lines.append(
            f"| [{run.slug}]({run.slug}/response.json) | {_cell(run.question)} | `{run.kind}` "
            f"| {_shown(run)} | {_cell(run.headline)} | {_cell(run.trials_matched)} | `{run.model}` |"
        )
    return "\n".join(lines) + "\n"


# The parts of a response that the README's abridged copy folds away.
_FOLDED: Final = (
    ("meta", "interpretation"),
    ("meta", "plan"),
    ("meta", "options"),
    ("meta", "planner"),
    ("meta", "cache"),
    ("meta", "timing"),
    ("meta", "debug"),
    ("meta", "suggested_followups"),
)
_LINE_WIDTH: Final = 130
_LONGEST_TEXT: Final = 100


def _cut(items: list[Any], keep: int, noun: str) -> list[Any]:
    return items if len(items) <= keep else [*items[:keep], f"... {len(items) - keep} more {noun}"]


def _shortened(node: Any) -> Any:
    """`node` with every string over 100 characters cut to its first 97 and three dots."""
    if isinstance(node, str):
        return node if len(node) <= _LONGEST_TEXT else node[: _LONGEST_TEXT - 3] + "..."
    if isinstance(node, list):
        return [_shortened(item) for item in node]
    if isinstance(node, dict):
        return {key: _shortened(value) for key, value in node.items()}
    return node


def _dump(value: Any, level: int = 0) -> str:
    """JSON with a container on one line when it fits, so that a channel or a citation stays readable."""
    flat = json.dumps(value, ensure_ascii=False)
    pad = "  " * level
    if not isinstance(value, dict | list) or not value or len(pad) + len(flat) <= _LINE_WIDTH:
        return flat
    if isinstance(value, dict):
        items = [f"{pad}  {json.dumps(key)}: {_dump(item, level + 1)}" for key, item in value.items()]
        return "{\n" + ",\n".join(items) + f"\n{pad}}}"
    return "[\n" + ",\n".join(f"{pad}  {_dump(item, level + 1)}" for item in value) + f"\n{pad}]"


def abridged_response(response: Mapping[str, Any]) -> str:
    """A real response cut to its shape: two rows with one citation each, one reference, the rest folded."""
    cut: dict[str, Any] = json.loads(json.dumps(response))
    if cut["visualization"] is not None:
        data = cut["visualization"]["data"]
        for rows in [data["nodes"], data["edges"]] if isinstance(data, dict) else [data]:
            for row in rows[:2]:
                row["citations"] = _cut(row["citations"], 1, "citations")
            rows[:] = _cut(rows, 2, "rows")
    references = cut["references"]
    shown = list(references)[:1]
    cut["references"] = {nct_id: references[nct_id] for nct_id in shown}
    if len(references) > len(shown):
        cut["references"]["..."] = f"{len(references) - len(shown)} more trials"
    for *parents, key in _FOLDED:
        node = cut
        for parent in parents:
            node = node[parent]
        node[key] = "..."
    if cut["meta"]["source"] is not None:
        cut["meta"]["source"]["requests"] = _cut(cut["meta"]["source"]["requests"], 0, "requests")
    return _dump(_shortened(cut))


def _run_summary(response: Mapping[str, Any]) -> str:
    """What the run did, from its own `meta`: the trials, the requests and the time it took."""
    meta = response["meta"]
    matched = sum(series["trials_matched"] for series in meta["counts"]["series"])
    timing = meta["timing"]
    visualization = response["visualization"]
    first = visualization["data"][0][visualization["encoding"]["x"]["field"]]
    return (
        f"The service matched {matched:,} trials with {len(meta['source']['requests'])} requests to "
        f"ClinicalTrials.gov and answered in {timing['total_ms'] / 1000:.1f} s, of which the model call took "
        f"{timing['plan_ms'] / 1000:.1f} s. The series starts in {first}, the first period with a trial; "
        "empty periods before it are left out."
    )


def response_example(directory: Path, slug: str = "01-assignment-request") -> str:
    """The README's account of one recorded run, as a sentence and a fenced abridged copy of its response."""
    response = json.loads((directory / slug / "response.json").read_text(encoding="utf-8"))
    return f"{_run_summary(response)}\n\n```json\n{abridged_response(response)}\n```"


# --- The README's generated blocks -----------------------------------------------------------------------

_MARKER: Final = re.compile(r"<!-- gen:(?P<name>[a-z-]+):start -->\n.*?<!-- gen:(?P=name):end -->", re.DOTALL)


def splice(text: str, blocks: Mapping[str, str]) -> str:
    """`text` with the content between each pair of `gen:NAME` markers replaced by `blocks[NAME]`.

    A pair that is missing, or a pair that no block belongs to, is an error: a hand-written README that
    loses a marker would otherwise stop being checked without a word.
    """
    present = {match["name"] for match in _MARKER.finditer(text)}
    if present != set(blocks):
        raise DocumentError(
            f"The README's generated blocks are {sorted(present)}, and docgen.py writes {sorted(blocks)}. "
            "Each needs a <!-- gen:NAME:start --> and a <!-- gen:NAME:end --> line."
        )

    def replace(match: re.Match[str]) -> str:
        name = match["name"]
        body = blocks[name].strip("\n")
        return f"<!-- gen:{name}:start -->\n{body}\n<!-- gen:{name}:end -->"

    return _MARKER.sub(replace, text)


def find_readme(root: Path) -> Path | None:
    """The README of `root`, found without regard to the case of its name."""
    return next((path for path in sorted(root.iterdir()) if path.name.lower() == "readme.md"), None)


def readme_blocks(examples_directory: Path, schema: Schema | None = None) -> dict[str, str]:
    """The text of every generated block of the README."""
    schema = schema if schema is not None else json_schema(Contract)
    return {
        "endpoints": endpoints_table(),
        "config": settings_table(),
        "request-schema": request_tables(schema),
        "response-types": response_types_table(schema),
        "response-example": response_example(examples_directory),
        "errors": errors_table(),
        "capabilities": capabilities_tables(),
        "examples": examples_tables(examples_directory, "docs/examples/"),
    }


# --- Every generated document ------------------------------------------------------------------------------


def generated_documents(root: Path = REPOSITORY_ROOT) -> dict[Path, str]:
    """Every generated document under `root`, as path to the text it should have."""
    docs, schema = root / "docs", json_schema(Contract)
    documents = {docs / "schema" / name: text for name, text in schema_documents().items()}
    documents[docs / "SCHEMA.md"] = schema_reference(schema)
    documents[docs / "examples" / "README.md"] = examples_index(docs / "examples")
    readme = find_readme(root)
    if readme is None:
        raise DocumentError(f"There is no README in {root}.")
    blocks = readme_blocks(docs / "examples", schema)
    documents[readme] = splice(readme.read_text(encoding="utf-8"), blocks)
    return documents


def stale_documents(root: Path = REPOSITORY_ROOT) -> list[Path]:
    """The generated documents that are missing or differ from what the code produces."""
    return [
        path
        for path, text in generated_documents(root).items()
        if not path.is_file() or path.read_text(encoding="utf-8") != text
    ]


def write_documents(root: Path = REPOSITORY_ROOT) -> list[Path]:
    """Write every generated document; return the paths."""
    documents = generated_documents(root)
    for path, text in documents.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return list(documents)
