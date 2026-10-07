"""Small stand-ins for the engine tests: field specs, study records, a fake registry client and plans.

The fake client evaluates the expressions the engine writes with the model of the server in
`engine_memory` (areas, ranges, MISSING, AND, OR and NOT), which is how the tests tell buckets, probes and
compared scopes apart.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from ctviz.catalog.fields import BoundDimension, Bucket, Evidence, FieldContext, FieldSpec, Value, Window
from ctviz.contract.plan import Pairing, PlanFilters, QueryPlan, Total
from ctviz.contract.response import Origin, UpstreamRequest
from ctviz.ctgov import essie
from ctviz.ctgov.client import Page, RequestLog, WalkResult
from ctviz.ctgov.params import BoundTerm, Params, Scope, canonical_url
from ctviz.ctgov.study import Study, parse_study
from ctviz.engine.lower import EnginePlan, MeasureSpec, RowSpec

from .engine_memory import _Parser

BASE = "https://registry.test/api/v2"
_DESIGN = "protocolSection.designModule"
_STATUS = "protocolSection.statusModule"


def study(
    number: int,
    *,
    phases: Sequence[str] = ("PHASE1",),
    start: str | None = "2020-03",
    posted: str = "2021-01-05",
    sponsor: str = "Acme",
    sponsor_class: str = "INDUSTRY",
    countries: Sequence[str] = (),
    drugs: Sequence[tuple[str, Sequence[str]]] = (),
    enrollment: int | None = None,
    completion: str | None = None,
) -> Study:
    """A trial record with only the fields the tests read."""
    status: dict[str, object] = {"studyFirstPostDateStruct": {"date": posted}}
    if start is not None:
        status["startDateStruct"] = {"date": start}
    if completion is not None:
        status["completionDateStruct"] = {"date": completion}
    interventions = [{"type": "DRUG", "name": name, "armGroupLabels": list(arms)} for name, arms in drugs]
    design: dict[str, object] = {"phases": list(phases)}
    if enrollment is not None:
        design["enrollmentInfo"] = {"count": enrollment}
    return parse_study(
        {
            "protocolSection": {
                "identificationModule": {"nctId": f"NCT{number:08d}", "briefTitle": f"Trial {number}"},
                "statusModule": status,
                "designModule": design,
                "sponsorCollaboratorsModule": {"leadSponsor": {"name": sponsor, "class": sponsor_class}},
                "contactsLocationsModule": {"locations": [{"country": name} for name in countries]},
                "armsInterventionsModule": {"interventions": interventions},
            }
        }
    )


# --- field specs ------------------------------------------------------------------------------------------


def _extract_phase(study: Study, _: FieldContext | None, __: BoundDimension) -> Sequence[Value]:
    return [
        Value(phase, phase, (Evidence(f"{_DESIGN}.phases[{index}]", phase),))
        for index, phase in enumerate(study.phases[:1])
    ]


def _phase_buckets(_: BoundDimension, __: Window | None) -> Sequence[Bucket]:
    return [
        Bucket("PHASE1", "Phase 1", essie.area("Phase", "PHASE1")),
        Bucket("PHASE2", "Phase 2", essie.area("Phase", "PHASE2")),
        Bucket("NONE", "No phase listed", essie.missing("Phase")),
    ]


PHASE = FieldSpec(
    key="phase", title="Phase", kind="category", pieces=("Phase",), extract=_extract_phase,
    is_exclusive=True, is_ordinal=True, buckets=_phase_buckets,
    missing=Bucket("NONE", "No phase listed", essie.missing("Phase")),
)  # fmt: skip


def _extract_year(study: Study, _: FieldContext | None, __: BoundDimension) -> Sequence[Value]:
    if study.start_date is None:
        return []
    day = study.start_date.date
    return [Value(day[:4], day[:4], (Evidence(f"{_STATUS}.startDateStruct.date", day),))]


def _year_buckets(_: BoundDimension, window: Window | None) -> Sequence[Bucket]:
    assert window is not None
    return [
        Bucket(str(year), str(year), essie.range_("StartDate", f"{year}-01-01", f"{year}-12-31"))
        for year in range(int(window.first), int(window.last) + 1)
    ]


START_DATE = FieldSpec(
    key="start_date", title="Start date", kind="date", pieces=("StartDate", "StartDateType"),
    extract=_extract_year, is_exclusive=True, is_ordinal=True, buckets=_year_buckets,
)  # fmt: skip


def _extract_country(study: Study, _: FieldContext | None, __: BoundDimension) -> Sequence[Value]:
    return [
        Value(
            location.country,
            location.country,
            (
                Evidence(
                    f"protocolSection.contactsLocationsModule.locations[{location.index}].country",
                    location.country,
                ),
            ),
        )
        for location in study.locations
        if location.country is not None
    ]


COUNTRY = FieldSpec(
    key="country", title="Country", kind="entity", pieces=("LocationCountry",), extract=_extract_country,
    is_exclusive=False, is_ordinal=False,
)  # fmt: skip


def _extract_drug(study: Study, _: FieldContext | None, __: BoundDimension) -> Sequence[Value]:
    return [
        Value(
            intervention.name.lower(),
            intervention.name,
            (
                Evidence(
                    f"protocolSection.armsInterventionsModule.interventions[{intervention.index}].name",
                    intervention.name,
                ),
            ),
            frozenset(intervention.arm_group_labels),
        )
        for intervention in study.interventions
        if intervention.name is not None
    ]


DRUG = FieldSpec(
    key="drug", title="Drug", kind="entity", pieces=("InterventionName",), extract=_extract_drug,
    is_exclusive=False, is_ordinal=False,
)  # fmt: skip


def _extract_sponsor(study: Study, _: FieldContext | None, __: BoundDimension) -> Sequence[Value]:
    name = study.lead_sponsor_name
    path = "protocolSection.sponsorCollaboratorsModule.leadSponsor.name"
    return [] if name is None else [Value(name, name, (Evidence(path, name),))]


SPONSOR = FieldSpec(
    key="sponsor", title="Sponsor", kind="entity", pieces=("LeadSponsorName",), extract=_extract_sponsor,
    is_exclusive=True, is_ordinal=False,
)  # fmt: skip


def bound(spec: FieldSpec, role: Literal["axis", "series", "node"] = "axis") -> BoundDimension:
    return BoundDimension(spec=spec, time_unit="year" if spec.kind == "date" else None, role=role)


# --- plans and scopes -------------------------------------------------------------------------------------

PUBLIC_PLAN = QueryPlan(
    interpretation="A test question.",
    entities=[],
    filters=PlanFilters(
        phases=[], statuses=[], exclude_statuses=[], study_types=[], sponsor_classes=[],
        intervention_types=[],
        sexes=[], age_groups=[], allocations=[], maskings=[], primary_purposes=[], has_results=[],
        intervention_models=[], evidence=[],
        date_field=None, year_from=None, year_to=None,
    ),
    analysis=Total(kind="total", statistic=None, of=None),
    chart_preference=None,
    unapplied=[],
)  # fmt: skip


def scope(label: str | None = None, *, sponsor: str | None = None, scope_id: str = "s0") -> Scope:
    terms: tuple[BoundTerm, ...] = ()
    if sponsor is not None:
        terms = (
            BoundTerm(
                kind="sponsor", text=sponsor, term=sponsor, parameter=None,
                expr=essie.area("LeadSponsorName", sponsor), definition="lead_sponsor_search", note="",
            ),
        )  # fmt: skip
    return Scope(id=scope_id, label=label, terms=terms, enum_filters={}, date_range=None)


def engine_plan(
    *dimensions: BoundDimension,
    scopes: tuple[Scope, ...] | None = None,
    relation: Literal["series", "network"] | None = None,
    pairing: Pairing = "same_trial",
    window: Window | None = None,
    top_n: int = 15,
    citations: int = 2,
    rows: RowSpec | None = None,
    measure: MeasureSpec | None = None,
) -> EnginePlan:
    return EnginePlan(
        scopes=scopes or (scope(),),
        compare_kind=None,
        dimensions=dimensions,
        relation=relation,
        pairing=pairing,
        rows=rows,
        window=window,
        top_n=top_n,
        max_series=10,
        min_link_weight=2,
        max_links=60,
        chart_preference=None,
        citations_per_datum=citations,
        public=PUBLIC_PLAN,
        measure=measure,
    )


# --- the fake registry ------------------------------------------------------------------------------------


@dataclass
class FakeContext:
    requests: list[UpstreamRequest] = field(default_factory=list)

    def log_request(self, entry: UpstreamRequest) -> None:
        self.requests.append(entry)

    def note_studies(self, studies: Iterable[Study]) -> None:
        pass


@dataclass
class FakeClient:
    """Answers the client's methods from a list of trials; `miscount` adds to a bucket's total."""

    studies: Sequence[Study]
    miscount: dict[str, int] = field(default_factory=dict)
    sizes: dict[Params, int] = field(default_factory=dict)  # counts of exact searches, for the resolver
    registry: int = 1_000_000

    def _matching(self, params: Params) -> list[Study]:
        if params.advanced is None:
            return list(self.studies)
        return [study for study in self.studies if _Parser.of(params.advanced, study).expr()]

    def _log(
        self,
        ctx: RequestLog,
        params: Params,
        origin: Origin,
        total: int,
        page_size: int,
        fields: Sequence[str],
        sort: str | None = None,
    ) -> str:
        url = canonical_url(BASE, params, count_total=True, page_size=page_size, fields=fields, sort=sort)
        ctx.log_request(
            UpstreamRequest(url=url, status=200, duration_ms=0, total_count=total, records_returned=0,
                            is_cached=False, origin=origin)
        )  # fmt: skip
        return url

    def _total(self, params: Params) -> int:
        known = self.sizes.get(params)
        extra = sum(add for expr, add in self.miscount.items() if expr in (params.advanced or ""))
        return (len(self._matching(params)) if known is None else known) + extra

    async def registry_size(self) -> int:
        return self.registry

    async def count(self, params: Params, ctx: RequestLog, *, origin: Origin) -> int:
        total = self._total(params)
        self._log(ctx, params, origin, total, 1, ("NCTId",))
        return total

    async def sample(
        self, params: Params, ctx: RequestLog, *, fields: Sequence[str], page_size: int, sort: str | None,
        origin: Origin,
    ) -> Page:  # fmt: skip
        matching = _sorted(self._matching(params), sort)
        total = self._total(params)
        url = self._log(ctx, params, origin, total, page_size, fields, sort)
        return Page(total=total, studies=tuple(matching[:page_size]), url=url)

    async def sorted_page(
        self, params: Params, ctx: RequestLog, *, fields: Sequence[str], sort: str, page_size: int
    ) -> Page:
        return await self.sample(
            params, ctx, fields=fields, page_size=page_size, sort=sort, origin="execution"
        )

    async def walk(self, params: Params, ctx: RequestLog, *, fields: Sequence[str]) -> WalkResult:
        matching = self._matching(params)
        self._log(ctx, params, "execution", len(matching), 1000, fields)
        return WalkResult(tuple(matching), self._total(params))


def _sorted(studies: list[Study], sort: str | None) -> list[Study]:
    if sort == "StudyFirstPostDate:desc":
        return sorted(
            studies,
            key=lambda s: (s.first_post_date.date if s.first_post_date else "", s.nct_id),
            reverse=True,
        )
    return studies
