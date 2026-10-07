"""Entity resolution: what the registry makes of the words the question names, counted exactly.

Every entity is searched under the definition the plan states and, where it helps, under another reading
(a drug name read as a condition). The counts decide whether the chart is drawn, drawn with a warning, or
replaced by an explanation. The entities then become scopes, one per compared group, each probed once.
"""

import dataclasses
from collections.abc import Awaitable, Callable, Mapping
from typing import Final, Literal, Protocol, cast

from ctviz.contract.plan import Aggregate, DateField, Entity, EntityKind, QueryPlan
from ctviz.contract.request import QueryRequest
from ctviz.contract.response import (
    Clarification,
    EntityResolution,
    MatchDefinition,
    Note,
    OtherReading,
    Outcome,
)
from ctviz.ctgov import essie
from ctviz.ctgov.client import RequestLog
from ctviz.ctgov.params import BoundTerm, DateRange, Params, Scope
from ctviz.engine.gather import gather
from ctviz.engine.lower import PlannedLike, Resolved
from ctviz.engine.registry import Registry

# Another reading must be this many times larger than the one used before the answer says so.
_OTHER_READING_FACTOR: Final = 5
_MAX_RELAXED_COUNTS: Final = 4

_REQUEST_FIELD_OF: Final[Mapping[EntityKind, str]] = {
    "drug": "drug_name",
    "condition": "condition",
    "sponsor": "sponsor",
    "country": "country",
    "term": "term",
}
_FILTER_KEY_OF: Final = {
    "phases": "phase",
    "statuses": "overall_status",
    "study_types": "study_type",
    "sponsor_classes": "sponsor_class",
    "intervention_types": "intervention_type",
}
_FILTER_NAME_OF: Final = {
    "phase": "the phase filter",
    "overall_status": "the status filter",
    "study_type": "the study type filter",
    "sponsor_class": "the sponsor class filter",
    "intervention_type": "the intervention type filter",
}
_DATE_PIECE_OF: Final = {
    "start_date": "StartDate",
    "primary_completion_date": "PrimaryCompletionDate",
    "completion_date": "CompletionDate",
    "first_posted_date": "StudyFirstPostDate",
}


DrugMatch = Literal["broad", "name_only"]
ResolutionStatus = Literal["ok", "low_match", "ambiguous", "no_match", "matches_everything"]
_PARAMETER_OF: Final[Mapping[MatchDefinition, str]] = {
    "intervention_search": "query.intr",
    "condition_search": "query.cond",
    "lead_sponsor_search": "query.lead",
    "term_search": "query.term",
}
_DEFINITION: Final[Mapping[EntityKind, MatchDefinition]] = {
    "drug": "intervention_search",
    "condition": "condition_search",
    "sponsor": "lead_sponsor_search",
    "country": "country_exact",
    "term": "term_search",
}


class CountryLookup(Protocol):
    """The country table: the registry's spelling of a name or code, or None when it is not a country."""

    def resolve(self, text: str) -> str | None: ...


class ResolveDeps(Protocol):
    """The part of the pipeline's dependencies that resolution uses."""

    @property
    def ctgov(self) -> Registry: ...

    @property
    def resolver(self) -> "EntityResolver": ...


class EntityResolver:
    """Counts what an entity matches, under its own definition and under another reading."""

    def __init__(self, client: Registry, countries: CountryLookup, *, low_match_threshold: int) -> None:
        self._client = client
        self._countries = countries
        self._low_match_threshold = low_match_threshold

    async def resolve(
        self, kind: EntityKind, text: str, ctx: RequestLog, *, drug_match: DrugMatch
    ) -> EntityResolution:
        """Raises ValueError when the text has no letter or digit to search for."""
        if kind == "country":
            return await self._country(text, ctx)
        term = essie.literal(text)
        primary = Params(((_PARAMETER_OF[_DEFINITION[kind]], term),))
        strict: int | None = None
        other_readings: list[OtherReading] = []
        if kind == "drug":
            broad, strict, as_condition = await self._counts(
                ctx,
                primary,
                Params(advanced=essie.area("InterventionName", text)),
                Params((("query.cond", term),)),
            )
            other_readings.append(OtherReading(kind="condition", trials_matched=as_condition))
        elif kind == "condition":
            broad, as_drug = await self._counts(ctx, primary, Params((("query.intr", term),)))
            other_readings.append(OtherReading(kind="drug", trials_matched=as_drug))
        else:
            (broad,) = await self._counts(ctx, primary)

        use_name_only = kind == "drug" and drug_match == "name_only"
        matched = strict if use_name_only and strict is not None else broad
        return EntityResolution(
            kind=kind,
            source="question",
            text=text,
            term_searched=term,
            definition="intervention_name" if use_name_only else _DEFINITION[kind],
            status=await self._status(kind, matched),
            trials_matched=matched,
            strict_name_matches=strict,
            other_readings=other_readings,
            candidates=[],
        )

    async def _country(self, text: str, ctx: RequestLog) -> EntityResolution:
        name = self._countries.resolve(text) or text
        matched = await self._client.count(
            Params(advanced=essie.area("LocationCountry", name)), ctx, origin="resolution"
        )
        return EntityResolution(
            kind="country",
            source="question",
            text=text,
            term_searched=name,
            definition="country_exact",
            status=await self._status("country", matched),
            trials_matched=matched,
            strict_name_matches=None,
            other_readings=[],
            candidates=[],
        )

    async def _counts(self, ctx: RequestLog, *searches: Params) -> list[int]:
        return await gather([_counter(self._client, params, ctx) for params in searches])

    async def _status(self, kind: EntityKind, matched: int) -> ResolutionStatus:
        if matched == 0:
            return "no_match"
        if matched >= await self._client.registry_size():
            return "matches_everything"
        if kind in ("drug", "condition") and matched < self._low_match_threshold:
            return "low_match"
        return "ok"


def bind_term(resolution: EntityResolution) -> BoundTerm:
    """The entity as the registry will be asked for it: a search parameter, or an expression."""
    definition = resolution.definition
    text, count = resolution.text, resolution.trials_matched
    if definition == "country_exact":
        expr, parameter = essie.area("LocationCountry", resolution.term_searched), None
        note = f"Trials with at least one site in {resolution.term_searched}."
    elif definition == "intervention_name":
        expr, parameter = essie.area("InterventionName", text), None
        note = f"Matched '{text}' in intervention names and their synonyms only."
    else:
        expr, parameter = None, _PARAMETER_OF[definition]
        note = _search_note(resolution, count)
    return BoundTerm(
        kind=resolution.kind,
        text=text,
        term=resolution.term_searched if expr is None else essie.literal(text),
        parameter=parameter,
        expr=expr,
        definition=definition,
        note=note,
    )


async def resolve_entities(planned: PlannedLike, deps: ResolveDeps, ctx: RequestLog) -> Resolved | Outcome:
    """Resolve every entity, build the scopes and probe each once.

    Returns an `Outcome` when nothing can be drawn: an entity that matches nothing or everything, or a
    scope set in which no scope matches a trial. A compared group that matches nothing stays a scope.
    """
    plan = planned.plan
    try:
        resolutions = await gather(
            [_resolver_call(deps, entity, ctx, planned.options.drug_match) for entity in plan.entities]
        )
    except ValueError as error:
        return _clarify(f"A name in the question could not be searched: {error}")
    resolutions = [
        resolution.model_copy(update={"source": _source(planned.request, entity)})
        for entity, resolution in zip(plan.entities, resolutions, strict=True)
    ]
    for entity, resolution in zip(plan.entities, resolutions, strict=True):
        if outcome := _entity_outcome(entity, resolution):
            return outcome

    terms = [bind_term(resolution) for resolution in resolutions]
    scopes = _scopes(plan, plan.entities, terms)
    counts = await gather([_probe_call(deps, scope, ctx) for scope in scopes])
    matched = {scope.id: count for scope, count in zip(scopes, counts, strict=True)}
    if not any(matched.values()):
        return await _nothing_matches(scopes, deps, ctx)
    return Resolved(
        entities=tuple(resolutions),
        scopes=scopes,
        matched=matched,
        warnings=tuple(_warnings(resolutions)),
        assumptions=tuple(term.note for term in terms),
    )


def _resolver_call(
    deps: ResolveDeps, entity: Entity, ctx: RequestLog, drug_match: DrugMatch
) -> Callable[[], Awaitable[EntityResolution]]:
    return lambda: deps.resolver.resolve(entity.kind, entity.value, ctx, drug_match=drug_match)


def _probe_call(deps: ResolveDeps, scope: Scope, ctx: RequestLog) -> Callable[[], Awaitable[int]]:
    return lambda: deps.ctgov.count(scope.params(), ctx, origin="probe")


def _counter(client: Registry, params: Params, ctx: RequestLog) -> Callable[[], Awaitable[int]]:
    return lambda: client.count(params, ctx, origin="resolution")


def _search_note(resolution: EntityResolution, count: int) -> str:
    text = resolution.text
    match resolution.definition:
        case "intervention_search":
            return (
                f"Matched '{text}' with the registry's intervention search (names, other names, arm labels, "
                f"titles, descriptions and MeSH terms, with synonyms): {count:,} trials. "
                f"{resolution.strict_name_matches or 0:,} of them name it as an intervention."
            )
        case "condition_search":
            return (
                f"Matched '{text}' with the registry's condition search "
                "(conditions, titles, keywords and MeSH terms, with synonyms)."
            )
        case "lead_sponsor_search":
            return (
                f"Matched '{text}' in the lead sponsor name. This is a text search, so related "
                "organisations that mention the name are included."
            )
        case _:
            return f"Matched '{text}' anywhere in the record."


def _source(request: QueryRequest | None, entity: Entity) -> str:
    if request is None:
        return "plan"
    given: list[str] = list(getattr(request, _REQUEST_FIELD_OF[entity.kind]) or [])
    if request.compare is not None:
        given.extend(request.compare.values)
    return "request_field" if entity.value.casefold() in {value.casefold() for value in given} else "question"


def _entity_outcome(entity: Entity, resolution: EntityResolution) -> Outcome | None:
    if resolution.status == "matches_everything":
        return _clarify(
            f"'{resolution.text}' matches every trial in the registry, so it cannot narrow anything. "
            "Please name something more specific.",
        )
    if resolution.status == "no_match" and entity.role == "filter":
        readings = " ".join(
            f"Read as a {other.kind}, it matches {other.trials_matched:,} trials."
            for other in resolution.other_readings
            if other.trials_matched
        )
        return Outcome(
            kind="no_data",
            reason="entity_matched_nothing",
            message=f"No trials match '{resolution.text}' as a {resolution.kind}. {readings}".strip(),
        )
    return None


def _clarify(message: str) -> Outcome:
    return Outcome(
        kind="clarification",
        reason="could_not_interpret",
        message=message,
        clarification=Clarification(reason="could_not_interpret", missing_fields=[], options=[]),
    )


def _warnings(resolutions: list[EntityResolution]) -> list[Note]:
    notes: list[Note] = []
    for resolution in resolutions:
        text, count = resolution.text, resolution.trials_matched
        if resolution.status == "low_match":
            notes.append(
                Note(
                    code="low_match_count",
                    message=f"Only {count} trials match '{text}'. "
                    "If this is a misspelling, correct it and ask again.",
                )
            )
        for other in resolution.other_readings:
            if count and other.trials_matched >= _OTHER_READING_FACTOR * count:
                notes.append(
                    Note(
                        code="other_reading_larger",
                        message=f"'{text}' matches {count:,} trials as a {resolution.kind} but "
                        f"{other.trials_matched:,} as a {other.kind}.",
                    )
                )
    return notes


def _scopes(plan: QueryPlan, entities: list[Entity], terms: list[BoundTerm]) -> tuple[Scope, ...]:
    shared = [term for entity, term in zip(entities, terms, strict=True) if entity.role == "filter"]
    compared = [term for entity, term in zip(entities, terms, strict=True) if entity.role == "compare"]
    enum_filters = {
        key: tuple(values)
        for family, key in _FILTER_KEY_OF.items()
        if (values := getattr(plan.filters, family))
    }
    date_range = _date_range(plan)
    groups: list[tuple[str | None, tuple[BoundTerm, ...]]] = (
        [(term.text, (*shared, term)) for term in compared] if compared else [(None, tuple(shared))]
    )
    return tuple(
        Scope(id=f"s{position}", label=label, terms=group, enum_filters=enum_filters, date_range=date_range)
        for position, (label, group) in enumerate(groups)
    )


def _date_range(plan: QueryPlan) -> DateRange | None:
    filters = plan.filters
    if filters.year_from is None and filters.year_to is None:
        return None
    piece = _DATE_PIECE_OF[filters.date_field or _axis_date(plan)]
    return DateRange(
        piece=piece,
        first_day=None if filters.year_from is None else f"{filters.year_from}-01-01",
        last_day=None if filters.year_to is None else f"{filters.year_to}-12-31",
    )


def _axis_date(plan: QueryPlan) -> DateField:
    """The date a year range applies to when the plan names none: the one on the time axis, else the start."""
    if isinstance(plan.analysis, Aggregate) and plan.analysis.dimension in _DATE_PIECE_OF:
        return cast(DateField, plan.analysis.dimension)
    return "start_date"


async def _nothing_matches(scopes: tuple[Scope, ...], deps: ResolveDeps, ctx: RequestLog) -> Outcome:
    """Say why no trial matches: for one scope, what is left when each constraint is dropped in turn."""
    if len(scopes) > 1:
        return Outcome(
            kind="no_data", reason="no_trials_matched", message="No trials match any of the compared groups."
        )
    scope = scopes[0]
    relaxed = _relaxations(scope)
    if len(relaxed) < 2:
        return Outcome(kind="no_data", reason="no_trials_matched", message="No trials match this question.")
    counts = await gather(
        [_probe_call(deps, relaxed_scope, ctx) for _, relaxed_scope in relaxed[:_MAX_RELAXED_COUNTS]]
    )
    hints = [
        f"Without {name}, {count:,} trials match."
        for (name, _), count in zip(relaxed, counts, strict=False)
        if count
    ]
    return Outcome(
        kind="no_data",
        reason="no_trials_matched",
        message=" ".join(["No trials match all of these together.", *hints]),
    )


def _relaxations(scope: Scope) -> list[tuple[str, Scope]]:
    """The scope with each of its constraints left out in turn, named for the explanation."""
    relaxed = [
        (f"'{term.text}'", dataclasses.replace(scope, terms=tuple(t for t in scope.terms if t is not term)))
        for term in scope.terms
    ]
    for key in scope.enum_filters:
        remaining = {k: v for k, v in scope.enum_filters.items() if k != key}
        relaxed.append((_FILTER_NAME_OF[key], dataclasses.replace(scope, enum_filters=remaining)))
    if scope.date_range is not None:
        relaxed.append(("the date range", dataclasses.replace(scope, date_range=None)))
    return relaxed
