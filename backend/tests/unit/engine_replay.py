"""A registry that replays real records under the real catalogue's expressions, and a request's memory.

`engine_support` answers three small field specs. `Replay` answers the bucket and probe expressions that
the catalogue and the strategy really write, so a fan-out, a walk and the builders can be compared on the
real records of `catalog_samples` without a network. It reads no expression: each bucket and probe of the
plan is registered with the predicate that selects its trials, and a request is answered with the trials
that satisfy every registered expression found in its `filter.advanced`.
"""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

from ctviz.catalog.fields import BoundDimension, Window
from ctviz.contract.response import Origin, UpstreamRequest
from ctviz.ctgov import essie
from ctviz.ctgov.client import Page, RequestLog, WalkResult
from ctviz.ctgov.params import Params, canonical_url
from ctviz.ctgov.study import Study
from ctviz.engine import periods
from ctviz.engine.lower import EnginePlan

BASE = "https://registry.test/api/v2/studies"
Predicate = Callable[[Study], bool]


@dataclass
class RequestMemory:
    """What a request remembers: the requests in the order issued, and the trials the registry returned."""

    requests: list[UpstreamRequest] = field(default_factory=list)
    studies: dict[str, Study] = field(default_factory=dict)

    # RequestLog
    def log_request(self, entry: UpstreamRequest) -> None:
        self.requests.append(entry)

    def note_studies(self, studies: Iterable[Study]) -> None:
        self.studies.update((study.nct_id, study) for study in studies)

    # Provenance
    def was_returned(self, nct_id: str) -> bool:
        return nct_id in self.studies

    def value_at(self, nct_id: str, path: str) -> str | None:
        found = self.studies[nct_id].value_at(path)
        if isinstance(found, bool):
            return "true" if found else "false"
        return None if found is None else str(found)

    def total_count(self, url: str) -> int | None:
        return next((entry.total_count for entry in self.requests if entry.url == url), None)


def leaves(plan: EnginePlan, window: Window | None) -> dict[str, Predicate]:
    """The expression of every bucket and probe the plan can issue, with the trials it selects."""
    found: dict[str, Predicate] = {}
    for dimension in plan.dimensions:
        spec = dimension.spec
        found[str(essie.missing(spec.pieces[0]))] = _has_none(dimension)
        if spec.kind == "date" and window is not None:
            piece = spec.pieces[0]
            first = periods.first_day(window.unit, window.first)
            last = periods.last_day(window.unit, window.last)
            day_before, day_after = first - timedelta(days=1), last + timedelta(days=1)
            found[str(essie.range_(piece, None, day_before.isoformat()))] = _starts_before(dimension, first)
            found[str(essie.range_(piece, day_after.isoformat(), None))] = _starts_after(dimension, last)
        for bucket in spec.buckets(dimension, window) if spec.buckets is not None else ():
            found[str(bucket.expr)] = _in_bucket(dimension, bucket.key)  # a bucket wins over a probe
    return found


def _in_bucket(dimension: BoundDimension, key: str) -> Predicate:
    return lambda study: any(value.key == key for value in dimension.spec.extract(study, None, dimension))


def _has_none(dimension: BoundDimension) -> Predicate:
    return lambda study: not dimension.spec.extract(study, None, dimension)


def _period_start(dimension: BoundDimension, study: Study) -> date | None:
    """The first day of the period that holds the study's date, a month read as its first day."""
    values = dimension.spec.extract(study, None, dimension)
    return periods.first_day(dimension.time_unit or "year", values[0].key) if values else None


def _starts_before(dimension: BoundDimension, day: date) -> Predicate:
    return lambda study: (start := _period_start(dimension, study)) is not None and start < day


def _starts_after(dimension: BoundDimension, day: date) -> Predicate:
    return lambda study: (start := _period_start(dimension, study)) is not None and start > day


@dataclass
class Replay:
    """The client's methods, answered from records held in memory. It logs each call like the client does."""

    studies: Sequence[Study]
    predicates: dict[str, Predicate] = field(default_factory=dict)
    size: int = 606_007

    @classmethod
    def for_plan(cls, studies: Sequence[Study], plan: EnginePlan, window: Window | None) -> "Replay":
        return cls(studies, leaves(plan, window))

    def _select(self, params: Params, sort: str | None) -> list[Study]:
        text = params.advanced or ""
        wanted = [predicate for expression, predicate in self.predicates.items() if expression in text]
        chosen = [study for study in self.studies if all(predicate(study) for predicate in wanted)]
        return _ordered(chosen, sort)

    def _log(
        self, ctx: RequestLog, params: Params, origin: Origin, total: int, returned: int, **query: object
    ) -> str:
        url = canonical_url(BASE, params, count_total=True, **query)  # type: ignore[arg-type]
        ctx.log_request(
            UpstreamRequest(
                url=url,
                status=200,
                duration_ms=0,
                total_count=total,
                records_returned=returned,
                is_cached=False,
                origin=origin,
            )
        )
        return url

    async def registry_size(self) -> int:
        return self.size

    async def count(self, params: Params, ctx: RequestLog, *, origin: Origin) -> int:
        total = len(self._select(params, None))
        self._log(ctx, params, origin, total, 1, page_size=1, fields=("NCTId",))
        return total

    async def sample(
        self,
        params: Params,
        ctx: RequestLog,
        *,
        fields: Sequence[str],
        page_size: int,
        sort: str | None,
        origin: Origin,
    ) -> Page:
        chosen = self._select(params, sort)
        page = tuple(chosen[:page_size])
        url = self._log(
            ctx, params, origin, len(chosen), len(page), page_size=page_size, fields=fields, sort=sort
        )
        ctx.note_studies(page)
        return Page(total=len(chosen), studies=page, url=url)

    async def sorted_page(
        self, params: Params, ctx: RequestLog, *, fields: Sequence[str], sort: str, page_size: int
    ) -> Page:
        return await self.sample(
            params, ctx, fields=fields, page_size=page_size, sort=sort, origin="execution"
        )

    async def walk(self, params: Params, ctx: RequestLog, *, fields: Sequence[str]) -> WalkResult:
        chosen = self._select(params, None)
        self._log(
            ctx, params, "execution", len(chosen), len(chosen), page_size=1000, fields=fields, sort=None
        )
        ctx.note_studies(chosen)
        return WalkResult(tuple(chosen), len(chosen))


def _ordered(studies: list[Study], sort: str | None) -> list[Study]:
    """Relevance is the order given; a sort key is a registry piece with a direction."""
    if sort is None or sort == "@relevance":
        return studies
    piece, _, direction = sort.partition(":")
    key = _SORT_KEYS[piece]
    return sorted(studies, key=lambda study: (key(study), study.nct_id), reverse=direction == "desc")


def _day(study_date: object) -> str:
    return getattr(study_date, "date", "") if study_date is not None else ""


_SORT_KEYS: dict[str, Callable[[Study], object]] = {
    "StudyFirstPostDate": lambda study: _day(study.first_post_date),
    "StartDate": lambda study: _day(study.start_date),
    "CompletionDate": lambda study: _day(study.completion_date),
    "EnrollmentCount": lambda study: study.enrollment_count or 0,
}
