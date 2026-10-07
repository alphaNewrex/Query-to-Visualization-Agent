"""An in-memory registry that evaluates the Essie the service writes, and the whole path after resolution.

`Registry` holds records and answers `count`, `sample`, `sorted_page` and `walk` for any `Params` the
engine builds, so a fan-out, a walk and the builders can be run on the same records without a network.
It models the server as the code's own comments describe it: ranges are inclusive, a `YYYY-MM` date is read
as its first day, `AREA[x]MISSING` is no value, `AREA[Phase]X` is "lists X", a quoted phrase is matched
without regard to case and accents (as the registry matches a state), and `SEARCH[Location](...)` holds
inside one site. `filter.overallStatus` takes one `A|B` list: anything else is answered with a 400, which
the client turns into an `AppError`.

Agreement with this model shows that the paths are consistent with each other, not what the live registry
does; the live checks are in the report that goes with the change.
"""

import dataclasses
import json
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import ctviz.catalog  # noqa: F401  (fills CATALOG)
from ctviz.applied import applied_filters
from ctviz.catalog.fields import CATALOG, BoundDimension
from ctviz.contract.plan import QueryPlan
from ctviz.contract.request import QueryRequest, RequestOptions
from ctviz.contract.response import (
    CacheInfo,
    PlannerInfo,
    Timing,
    UpstreamRequest,
    VisualizationResponse,
)
from ctviz.ctgov.client import ApiVersion, Page, WalkResult
from ctviz.ctgov.params import Params, Scope, canonical_url
from ctviz.ctgov.study import Study, parse_study
from ctviz.engine.execute import EngineResult, execute_plan
from ctviz.engine.handoff import present
from ctviz.engine.lower import EnginePlan
from ctviz.engine.overlap import shared_trials
from ctviz.engine.shape import ShapedResult, shape
from ctviz.engine.strategy import ExecutionPlan, Limits, choose_strategy
from ctviz.errors import UpstreamRejectedQuery
from ctviz.viz.meta import MetaContext, build_response

BASE = "https://registry.test/api/v2"
VERSION = ApiVersion(api_version="2.0.5", data_timestamp="2026-10-06T09:00:05")
_TOKEN = re.compile(
    r'\s*(AREA\[\w+\]|SEARCH\[\w+\]|RANGE\[[^\]]*\]|"[^"]*"|\(|\)|\b(?:AND|OR|NOT|MISSING)\b|[A-Za-z0-9_]+)'
)


def folded(text: str) -> str:
    """Case and accents folded away, which is how the registry compares a place name."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char)).casefold()


def _values(study: Study, piece: str) -> list[str]:
    one = {
        "StudyType": study.study_type,
        "OverallStatus": study.overall_status,
        "LeadSponsorClass": study.lead_sponsor_class,
        "LeadSponsorName": study.lead_sponsor_name,
        "Sex": study.sex,
        "DesignAllocation": study.allocation,
        "DesignMasking": study.masking,
        "DesignPrimaryPurpose": study.primary_purpose,
        "DesignInterventionModel": study.intervention_model,
        "StartDate": study.start_date.date if study.start_date else None,
        "CompletionDate": study.completion_date.date if study.completion_date else None,
        "PrimaryCompletionDate": study.primary_completion_date.date
        if study.primary_completion_date
        else None,
        "StudyFirstPostDate": study.first_post_date.date if study.first_post_date else None,
        "EnrollmentCount": None if study.enrollment_count is None else str(study.enrollment_count),
        "HasResults": None if study.has_results is None else str(study.has_results).lower(),
    }
    if piece in one:
        return [] if one[piece] is None else [one[piece]]
    many = {
        "Phase": list(study.phases),
        "StdAge": list(study.std_ages),
        "InterventionType": [i.type for i in study.interventions if i.type],
        "LocationCountry": [s.country for s in study.locations if s.country],
        "LocationState": [s.state for s in study.locations if s.state],
        "Condition": list(study.conditions),
    }
    return many[piece]


def _split_top(text: str, separator: str) -> list[str]:
    parts, depth, start = [], 0, 0
    for position, char in enumerate(text):
        depth += (char == "(") - (char == ")")
        if depth == 0 and text.startswith(separator, position):
            parts.append(text[start:position])
            start = position + len(separator)
    parts.append(text[start:])
    return parts


def _text_matches(text: str, haystack: str) -> bool:
    """A `query.*` value: terms joined with AND, each in parentheses or bare, matched as a substring."""
    text = text.strip()
    parts = _split_top(text, " AND ")
    if len(parts) > 1:
        return all(_text_matches(part, haystack) for part in parts)
    if text.startswith("(") and text.endswith(")") and len(_split_top(text[1:-1], "\0")) == 1:
        return _text_matches(text[1:-1], haystack)
    return text.casefold() in haystack


class _Parser:
    """Evaluates `filter.advanced` for one trial; `sites` is the list of (country, state) of its sites."""

    def __init__(self, tokens: list[str], study: Study) -> None:
        self.tokens, self.study, self.i = tokens, study, 0
        self.site: tuple[str | None, str | None] | None = None

    @classmethod
    def of(cls, text: str, study: Study) -> "_Parser":
        tokens = _TOKEN.findall(text)
        if "".join(tokens).replace(" ", "") != text.replace(" ", ""):
            raise ValueError(f"cannot tokenise {text!r}")
        return cls(tokens, study)

    def peek(self) -> str | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self) -> str:
        self.i += 1
        return self.tokens[self.i - 1]

    def expr(self) -> bool:
        value = self.term()
        while self.peek() in ("AND", "OR"):
            op = self.take()
            right = self.term()
            value = (value and right) if op == "AND" else (value or right)
        return value

    def term(self) -> bool:
        token = self.take()
        if token == "NOT":
            return not self.term()
        if token == "(":
            value = self.expr()
            assert self.take() == ")"
            return value
        if token.startswith("SEARCH["):
            return self.search()
        assert token.startswith("AREA["), token
        return self.atom(token[5:-1])

    def search(self) -> bool:
        """`SEARCH[Location](...)`: the inner expression holds at one site, every area read at that site."""
        assert self.take() == "("
        start = self.i
        depth = 1
        while depth:
            token = self.take()
            depth += (token == "(") - (token == ")")
        inner = self.tokens[start : self.i - 1]
        found = False
        for location in self.study.locations:
            sub = _Parser(inner, self.study)
            sub.site = (location.country, location.state)
            found = found or sub.expr()
        return found

    def have(self, piece: str) -> list[str]:
        if self.site is not None and piece in ("LocationCountry", "LocationState"):
            value = self.site[0] if piece == "LocationCountry" else self.site[1]
            return [] if value is None else [value]
        return _values(self.study, piece)

    def atom(self, piece: str) -> bool:
        have = self.have(piece)
        token = self.take()
        if token == "(":
            value = self.inner(have)
            assert self.take() == ")"
            return value
        return self.leaf(token, have)

    def inner(self, have: list[str]) -> bool:
        value = self.inner_term(have)
        while self.peek() in ("AND", "OR"):
            op = self.take()
            right = self.inner_term(have)
            value = (value and right) if op == "AND" else (value or right)
        return value

    def inner_term(self, have: list[str]) -> bool:
        token = self.take()
        return (not self.inner_term(have)) if token == "NOT" else self.leaf(token, have)

    def leaf(self, token: str, have: list[str]) -> bool:
        if token == "MISSING":
            return not have
        if token.startswith("RANGE["):
            low, high = token[6:-1].split(",")
            for raw in have:
                value = f"{raw}-01" if re.fullmatch(r"\d{4}-\d{2}", raw) else raw
                if re.fullmatch(r"\d+", value):
                    ok = (low == "MIN" or int(value) >= int(low)) and (
                        high == "MAX" or int(value) <= int(high)
                    )
                else:
                    ok = (low == "MIN" or value >= low) and (high == "MAX" or value <= high)
                if ok:
                    return True
            return False
        if token.startswith('"'):
            return folded(token.strip('"')) in {folded(item) for item in have}
        return token in have


def _text_haystacks(study: Study) -> dict[str, str]:
    interventions = [
        *(item.name or "" for item in study.interventions),
        *(other for item in study.interventions for other in item.other_names),
    ]
    return {
        "query.intr": " ".join(interventions).casefold(),
        "query.cond": " ".join(study.conditions).casefold(),
        "query.lead": (study.lead_sponsor_name or "").casefold(),
        "query.term": " ".join(
            [study.brief_title or "", *study.conditions, *interventions, study.lead_sponsor_name or ""]
        ).casefold(),
    }


def matches(study: Study, params: Params) -> bool:
    haystacks = _text_haystacks(study)
    for name, value in params.texts:
        if name == "filter.overallStatus":
            if not re.fullmatch(r"[A-Z_]+(\|[A-Z_]+)*", value):
                raise UpstreamRejectedQuery("ClinicalTrials.gov rejected a query that this service built.")
            if study.overall_status not in value.split("|"):
                return False
        elif not _text_matches(value, haystacks[name]):
            return False
    return params.advanced is None or _Parser.of(str(params.advanced), study).expr()


@dataclass
class Memory:
    """What a request remembers: the requests in the order issued, and the trials the registry returned."""

    requests: list[UpstreamRequest] = field(default_factory=list)
    studies: dict[str, Study] = field(default_factory=dict)

    def log_request(self, entry: UpstreamRequest) -> None:
        self.requests.append(entry)

    def note_studies(self, studies: Iterable[Study]) -> None:
        self.studies.update((s.nct_id, s) for s in studies)

    def was_returned(self, nct_id: str) -> bool:
        return nct_id in self.studies

    def value_at(self, nct_id: str, path: str) -> str | None:
        found = self.studies[nct_id].value_at(path)
        if found is None or isinstance(found, str):
            return found
        return json.dumps(found)

    def total_count(self, url: str) -> int | None:
        return next((e.total_count for e in self.requests if e.url == url), None)

    def counts_for(self, fragment: str) -> list[int | None]:
        return [e.total_count for e in self.requests if fragment in e.url]


@dataclass
class Registry:
    studies: Sequence[Study]
    one_page_max: int = 1000

    def _select(self, params: Params) -> list[Study]:
        return [s for s in self.studies if matches(s, params)]

    def _log(
        self, ctx: Any, params: Params, origin: Any, total: int, page_size: int, fields: Any, sort: Any = None
    ) -> str:
        url = canonical_url(BASE, params, count_total=True, page_size=page_size, fields=fields, sort=sort)
        ctx.log_request(
            UpstreamRequest(
                url=url,
                status=200,
                duration_ms=0,
                total_count=total,
                records_returned=0,
                is_cached=False,
                origin=origin,
            )
        )
        return url

    async def registry_size(self) -> int:
        return 600_000

    async def count(self, params: Params, ctx: Any, *, origin: Any) -> int:
        total = len(self._select(params))
        self._log(ctx, params, origin, total, 1, ("NCTId",))
        return total

    async def sample(
        self, params: Params, ctx: Any, *, fields: Any, page_size: int, sort: str | None, origin: Any
    ) -> Page:
        chosen = self._select(params)
        if sort and sort.startswith("EnrollmentCount"):
            chosen = sorted(chosen, key=lambda s: s.enrollment_count or 0, reverse=sort.endswith("desc"))
        url = self._log(ctx, params, origin, len(chosen), page_size, fields, sort)
        ctx.note_studies(chosen[:page_size])
        return Page(total=len(chosen), studies=tuple(chosen[:page_size]), url=url)

    async def sorted_page(self, params: Params, ctx: Any, *, fields: Any, sort: str, page_size: int) -> Page:
        return await self.sample(
            params, ctx, fields=fields, page_size=page_size, sort=sort, origin="execution"
        )

    async def walk(self, params: Params, ctx: Any, *, fields: Any) -> WalkResult:
        chosen = self._select(params)
        self._log(ctx, params, "execution", len(chosen), 1000, fields)
        ctx.note_studies(chosen)
        return WalkResult(tuple(chosen), len(chosen))


# --- records and plans -------------------------------------------------------------------------------------


def record(
    n: int,
    *,
    phases: Sequence[str] = ("PHASE2",),
    start: str | None = "2020-03",
    completion: str | None = None,
    completion_type: str | None = None,
    posted: str = "2021-01-05",
    sponsor: str = "Acme",
    types: Sequence[str] = ("DRUG",),
    names: Sequence[str] | None = None,
    countries: Sequence[str] = (),
    locations: Sequence[tuple[str | None, str | None]] | None = None,
    enrollment: int | None = None,
    enrollment_type: str | None = None,
    start_type: str | None = None,
    status: str = "COMPLETED",
    ages: Sequence[str] = ("ADULT",),
    conditions: Sequence[str] = ("Asthma",),
    arms: dict[str, list[str]] | None = None,
) -> Study:
    """A trial record with only the fields the tests read."""
    status_module: dict[str, object] = {
        "studyFirstPostDateStruct": {"date": posted},
        "overallStatus": status,
    }
    if start:
        status_module["startDateStruct"] = {"date": start, "type": start_type}
    if completion:
        status_module["completionDateStruct"] = {"date": completion, "type": completion_type}
    design: dict[str, object] = {"studyType": "INTERVENTIONAL"}
    if phases:
        design["phases"] = list(phases)  # a record with no phase has no key at all
    if enrollment is not None:
        design["enrollmentInfo"] = {"count": enrollment, "type": enrollment_type}
    given = list(names or [f"drug{n}-{i}" for i in range(len(types))])
    interventions = [
        {"type": t, "name": name, "armGroupLabels": list((arms or {}).get(name, ["A"]))}
        for t, name in zip(types, given, strict=True)
    ]
    sites = locations if locations is not None else [(c, None) for c in countries]
    return parse_study(
        {
            "protocolSection": {
                "identificationModule": {"nctId": f"NCT{n:08d}", "briefTitle": f"Trial {n}"},
                "statusModule": status_module,
                "designModule": design,
                "sponsorCollaboratorsModule": {"leadSponsor": {"name": sponsor, "class": "INDUSTRY"}},
                "contactsLocationsModule": {
                    "locations": [{"country": c, **({} if s is None else {"state": s})} for c, s in sites]
                },
                "armsInterventionsModule": {"interventions": interventions},
                "conditionsModule": {"conditions": list(conditions)},
                "eligibilityModule": {"stdAges": list(ages), "sex": "ALL"},
            },
            "hasResults": False,
        }
    )


def dim(key: str, role: Any = "axis", unit: Any = None) -> BoundDimension:
    spec = CATALOG[key]
    return BoundDimension(spec, (unit or "year") if spec.kind == "date" else None, role)


EMPTY_FILTERS: dict[str, Any] = dict(
    phases=[],
    statuses=[],
    exclude_statuses=[],
    study_types=[],
    sponsor_classes=[],
    intervention_types=[],
    sexes=[],
    age_groups=[],
    allocations=[],
    maskings=[],
    primary_purposes=[],
    has_results=[],
    intervention_models=[],
    evidence=[],
    date_field=None,
    year_from=None,
    year_to=None,
)


def public(analysis: dict[str, Any], **filters: Any) -> QueryPlan:
    return QueryPlan.model_validate(
        {
            "interpretation": "test",
            "entities": [],
            "filters": {**EMPTY_FILTERS, **filters},
            "analysis": analysis,
            "chart_preference": None,
            "unapplied": [],
        }
    )


def scope(label: str | None = None, scope_id: str = "s0", **kw: Any) -> Scope:
    return Scope(
        id=scope_id,
        label=label,
        terms=kw.pop("terms", ()),
        enum_filters=kw.pop("enum_filters", {}),
        date_range=kw.pop("date_range", None),
        **kw,
    )


def plan(
    *dims: BoundDimension,
    pub: QueryPlan,
    scopes: Sequence[Scope] | None = None,
    relation: Any = None,
    window: Any = None,
    top_n: int = 15,
    rows: Any = None,
    measure: Any = None,
    pairing: Any = "same_trial",
    compare_kind: Any = None,
) -> EnginePlan:
    return EnginePlan(
        scopes=tuple(scopes or (scope(),)),
        compare_kind=compare_kind,
        dimensions=dims,
        relation=relation,
        pairing=pairing,
        rows=rows,
        window=window,
        top_n=top_n,
        max_series=10,
        min_link_weight=2,
        max_links=60,
        chart_preference=None,
        citations_per_datum=2,
        public=pub,
        measure=measure,
    )


# --- the whole path after resolution -------------------------------------------------------------------


@dataclass
class Run:
    """Everything a run of the engine produced, from the strategy to the response."""

    plan: EnginePlan
    execution: ExecutionPlan
    result: EngineResult
    shaped: ShapedResult
    response: VisualizationResponse
    memory: Memory


async def execute(
    p: EnginePlan, studies: Sequence[Study], *, one_page_max: int = 1000, prefer_walk: bool = False
) -> tuple[ExecutionPlan, EngineResult, Memory, Registry]:
    client, ctx = Registry(studies, one_page_max), Memory()
    matched = {s.id: await client.count(s.params(), ctx, origin="probe") for s in p.scopes}
    xp = choose_strategy(p, matched, Limits(one_page_max, 300), prefer_walk=prefer_walk)
    assert isinstance(xp, ExecutionPlan), xp
    return xp, await execute_plan(p, xp, client, ctx), ctx, client


async def answer(
    p: EnginePlan, studies: Sequence[Study], *, one_page_max: int = 1000, prefer_walk: bool = False
) -> Run:
    """Strategy, execution, shaping, hand-over and the builders, as `pipeline._run` chains them."""
    xp, result, ctx, client = await execute(p, studies, one_page_max=one_page_max, prefer_walk=prefer_walk)
    matched = {run.scope.id: run.matched for run in xp.runs}
    shaped = shape(result, p)
    assert shaped.outcome is None, shaped.outcome
    shown = present(shaped, p)
    overlap = await shared_trials(p, matched, client, ctx)
    drawn = dataclasses.replace(
        shown.shaped,
        trials_in_several_series=overlap.shared,
        warnings=(*shown.shaped.warnings, *overlap.warnings),
    )
    context = MetaContext(
        request_id="0f3c7c1e-0000-0000-0000-000000000000",
        generated_at=datetime(2026, 10, 6, 12, tzinfo=UTC),
        query="q",
        request=QueryRequest(query="a question"),
        filters=applied_filters(p.public),
        plan=p.public,
        options=RequestOptions(),
        planner=PlannerInfo(
            mode="llm",
            model=None,
            reasoning_effort=None,
            prompt_version="x",
            attempts=1,
            is_repaired=False,
            is_fallback=False,
            usage=None,
        ),
        timing=Timing(total_ms=1, plan_ms=1, resolve_ms=1, fetch_ms=1, build_ms=0),
        cache=CacheInfo(is_plan_cached=False, is_response_cached=False, cached_at=None),
        strategy=shaped.steps,
    )
    response = build_response(context, shown.plan, drawn, VERSION, titles={})
    assert isinstance(response, VisualizationResponse), response
    return Run(p, xp, result, shaped, response, ctx)


def rows_of(response: VisualizationResponse) -> list[dict[str, Any]]:
    """The data rows of a response without the three reserved keys."""
    data = response.visualization.data
    items = data if isinstance(data, list) else [*data.nodes, *data.edges]
    reserved = {"citations", "citation_count", "source_url"}
    return [{k: v for k, v in item.model_dump().items() if k not in reserved} for item in items]
