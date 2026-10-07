"""Entity resolution: what the registry makes of the words the question names, counted exactly.

Every entity is searched under the definition the plan states and, where it helps, under another reading
(a drug name read as a condition). The counts decide whether the chart is drawn, drawn with a warning, or
replaced by an explanation. The entities then become scopes, one per compared group, each probed once.
"""

import dataclasses
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping
from typing import Final, Literal, Protocol, cast

from ctviz.contract.plan import FAMILY_DIMENSIONS, Aggregate, DateField, Entity, EntityKind, QueryPlan
from ctviz.contract.request import QueryRequest
from ctviz.contract.response import (
    Adjustment,
    Clarification,
    EntityResolution,
    MatchDefinition,
    Note,
    OtherReading,
    Outcome,
    RegistryTerm,
)
from ctviz.ctgov import essie
from ctviz.ctgov.client import RequestLog
from ctviz.ctgov.params import BoundTerm, DateRange, Params, Scope
from ctviz.engine.gather import gather
from ctviz.engine.lower import PlannedLike, Resolved
from ctviz.engine.registry import Registry
from ctviz.errors import UpstreamRejectedQuery

# Another reading must be this many times larger than the one used before the answer says so.
_OTHER_READING_FACTOR: Final = 5
_MAX_RELAXED_COUNTS: Final = 4
# A name that is in the intervention names of fewer than this share of the trials the intervention search
# matched is mostly matched through titles, descriptions and MeSH terms, as a class or a topic would be.
_NAME_SHARE_FLOOR: Final = 0.2
# The registry's own term for the matched trials is reported when it covers this many times more trials.
_REGISTRY_TERM_FACTOR: Final = 2
_VOCABULARY_SAMPLE: Final = 50
_VOCABULARY_MIN_SHARE: Final = 0.3
_BROWSE_PIECE: Final = {"drug": "InterventionBrowseModule", "condition": "ConditionBrowseModule"}
_BROWSE_KEY: Final = {"drug": "interventionBrowseModule", "condition": "conditionBrowseModule"}
_MESH_AREA: Final = {"drug": "InterventionMeshTerm", "condition": "ConditionMeshTerm"}

_REQUEST_FIELD_OF: Final[Mapping[EntityKind, str]] = {
    "drug": "drug_name",
    "condition": "condition",
    "sponsor": "sponsor",
    "country": "country",
    "term": "term",
}
_FILTER_KEY_OF: Final = FAMILY_DIMENSIONS
_FILTER_NAME_OF: Final = {
    "phase": "the phase filter",
    "overall_status": "the status filter",
    "study_type": "the study type filter",
    "sponsor_class": "the sponsor class filter",
    "intervention_type": "the intervention type filter",
    "sex": "the sex filter",
    "age_group": "the age group filter",
    "allocation": "the allocation filter",
    "masking": "the masking filter",
    "primary_purpose": "the primary purpose filter",
    "has_results": "the results filter",
    "intervention_model": "the intervention model filter",
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
        self,
        kind: EntityKind,
        text: str,
        ctx: RequestLog,
        *,
        drug_match: DrugMatch,
        may_reread: bool = True,
    ) -> EntityResolution:
        """Raises ValueError when the text has no letter or digit to search for.

        `may_reread` lets the registry's counts decide between drug, condition and term for a name the
        model classified. A kind the client wrote, in a field of the request or in a supplied plan, stays.
        """
        if kind == "country":
            return await self._country(text, ctx)
        term = essie.literal(text)
        if kind == "sponsor":
            (matched,) = await self._counts(ctx, Params((("query.lead", term),)))
            return self._resolution(
                kind, text, term, "lead_sponsor_search", matched, await self._status(kind, matched)
            )
        return await self._free_text(kind, text, term, ctx, drug_match, may_reread)

    async def _free_text(
        self,
        planned: EntityKind,
        text: str,
        term: str,
        ctx: RequestLog,
        drug_match: DrugMatch,
        may_reread: bool,
    ) -> EntityResolution:
        """A drug, a condition or a term: counted under each reading, then read as the one the registry holds.

        The plan's kind is the model's guess and the model is not always the same; the counts are the
        registry's. Each reading is counted in the field that would hold the name (intervention names, the
        conditions list) and broadly (the registry's own search). The reading with the larger field count
        wins; the plan's kind decides only when neither field holds the words, or when they tie.
        """
        broad_drug, name_drug, broad_cond, name_cond, anywhere = await self._counts(
            ctx,
            Params((("query.intr", term),)),
            Params(advanced=essie.area("InterventionName", text)),
            Params((("query.cond", term),)),
            Params(advanced=essie.area("Condition", text)),
            Params((("query.term", term),)),
        )
        kind = _read_as(planned, name_drug, name_cond) if may_reread else planned
        definition: MatchDefinition
        if kind == "drug":
            matched, definition = broad_drug, "intervention_search"
            others = [OtherReading(kind="condition", trials_matched=broad_cond)]
            if drug_match == "name_only":
                matched, definition = name_drug, "intervention_name"
        elif kind == "condition":
            matched, definition = broad_cond, "condition_search"
            others = [OtherReading(kind="drug", trials_matched=broad_drug)]
        else:
            matched, definition = anywhere, "term_search"
            others = [
                OtherReading(kind="drug", trials_matched=broad_drug),
                OtherReading(kind="condition", trials_matched=broad_cond),
            ]
        registry_term = None if kind == "term" else await self._registry_term(kind, term, matched, ctx)
        return self._resolution(
            kind,
            text,
            term,
            definition,
            matched,
            await self._status(kind, matched),
            planned=planned,
            strict=name_drug,
            condition_name=name_cond,
            registry_term=registry_term,
            other_readings=others,
        )

    async def _registry_term(
        self, kind: EntityKind, term: str, matched: int, ctx: RequestLog
    ) -> RegistryTerm | None:
        """The MeSH term the registry most often gives the matched trials, when it covers far more of them.

        A wording the registry does not link to its own vocabulary (an abbreviation, a local spelling)
        matches fewer trials than the vocabulary term that its matches carry; the difference is reported.
        """
        parameter = "query.cond" if kind == "condition" else "query.intr"
        page = await self._client.sample(
            Params(((parameter, term),)),
            ctx,
            fields=[_BROWSE_PIECE[kind]],
            page_size=_VOCABULARY_SAMPLE,
            sort=None,
            origin="resolution",
        )
        terms = Counter(name for study in page.studies for name in _mesh_terms(study.raw, kind))
        if not terms or not page.studies:
            return None
        name, holders = terms.most_common(1)[0]
        share = holders / len(page.studies)
        if share < _VOCABULARY_MIN_SHARE:
            return None
        covered = await self._client.count(
            Params(advanced=essie.area(_MESH_AREA[kind], name)), ctx, origin="resolution"
        )
        if covered < _REGISTRY_TERM_FACTOR * max(matched, 1):
            return None
        return RegistryTerm(term=name, trials_matched=covered, sample_share=round(share, 2))

    def _resolution(
        self,
        kind: EntityKind,
        text: str,
        term: str,
        definition: MatchDefinition,
        matched: int,
        status: ResolutionStatus,
        *,
        planned: EntityKind | None = None,
        strict: int | None = None,
        condition_name: int | None = None,
        registry_term: RegistryTerm | None = None,
        other_readings: list[OtherReading] | None = None,
    ) -> EntityResolution:
        return EntityResolution(
            kind=kind,
            planned_kind=planned or kind,
            source="question",
            text=text,
            term_searched=term,
            definition=definition,
            status=status,
            trials_matched=matched,
            strict_name_matches=strict,
            condition_name_matches=condition_name,
            registry_term=registry_term,
            other_readings=other_readings or [],
            candidates=[],
        )

    async def _country(self, text: str, ctx: RequestLog) -> EntityResolution:
        name = self._countries.resolve(text) or text
        matched = await self._client.count(
            Params(advanced=essie.area("LocationCountry", name)), ctx, origin="resolution"
        )
        return self._resolution(
            "country", text, name, "country_exact", matched, await self._status("country", matched)
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


_SEARCH_AREA: Final[Mapping[MatchDefinition, str]] = {
    "intervention_search": "InterventionSearch",
    "condition_search": "ConditionSearch",
    "lead_sponsor_search": "LeadSponsorName",
    "term_search": "BasicSearch",
}


def _read_as(planned: EntityKind, name_drug: int, name_condition: int) -> EntityKind:
    """The kind a free-text name is read as: the field that holds it more often, else the plan's kind."""
    if name_drug > name_condition:
        return "drug"
    if name_condition > name_drug:
        return "condition"
    return planned


def _mesh_terms(record: Mapping[str, object], kind: EntityKind) -> list[str]:
    """The MeSH terms the registry lists for a record's interventions or conditions."""
    derived = record.get("derivedSection")
    module = derived.get(_BROWSE_KEY[kind]) if isinstance(derived, Mapping) else None
    meshes = module.get("meshes") if isinstance(module, Mapping) else None
    if not isinstance(meshes, list):
        return []
    return [
        mesh["term"] for mesh in meshes if isinstance(mesh, Mapping) and isinstance(mesh.get("term"), str)
    ]


def bind_excluded(resolution: EntityResolution) -> BoundTerm:
    """The entity as an expression that selects the trials to leave out, searched as `bind_term` would."""
    definition, text = resolution.definition, resolution.text
    if definition == "country_exact":
        inner = essie.area("LocationCountry", resolution.term_searched)
    elif definition == "intervention_name":
        inner = essie.area("InterventionName", text)
    elif len(resolution.term_searched.split()) > 1:
        # What is left out is the phrase the user wrote, not every trial that holds its words apart.
        inner = essie.search_phrase(_SEARCH_AREA[definition], text)
    else:
        inner = essie.search(_SEARCH_AREA[definition], resolution.term_searched)
    return BoundTerm(
        kind=resolution.kind,
        text=text,
        term=resolution.term_searched,
        parameter=None,
        expr=essie.not_(inner),
        definition=definition,
        note=f"Trials that match '{text}' as a {resolution.kind} are left out"
        + (" (the whole phrase, in order)." if len(resolution.term_searched.split()) > 1 else "."),
    )


def bind_term(resolution: EntityResolution) -> BoundTerm:
    """The entity as the registry will be asked for it: a search parameter, or an expression."""
    definition = resolution.definition
    text, count = resolution.text, resolution.trials_matched
    if definition == "country_exact":
        expr, parameter = essie.area("LocationCountry", resolution.term_searched), None
        note = f"Trials with at least one site in {resolution.term_searched}."
    elif definition == "intervention_name":
        expr, parameter = essie.area("InterventionName", text), None
        note = f"{_reading_note(resolution)}Matched '{text}' in intervention names and their synonyms only."
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
            [
                _resolver_call(deps, entity, ctx, planned.options.drug_match, planned.request)
                for entity in plan.entities
            ]
        )
    except ValueError as error:
        return _clarify(f"A name in the question could not be searched: {error}")
    except _RejectedName as error:
        return _clarify(
            f"ClinicalTrials.gov could not read '{error.text}' as a search term, so nothing was counted. "
            "Rephrase it in plain words, without brackets or search operators."
        )
    resolutions = [
        resolution.model_copy(update={"source": _source(planned.request, entity)})
        for entity, resolution in zip(plan.entities, resolutions, strict=True)
    ]
    adjustments = tuple(
        Adjustment(
            code="entity_kind", path=f"/entities/{index}/kind", message=_kind_message(r), action="replaced"
        )
        for index, r in enumerate(resolutions)
        if r.kind != r.planned_kind
    )
    plan = plan.model_copy(
        update={
            "entities": [
                entity.model_copy(update={"kind": resolution.kind})
                for entity, resolution in zip(plan.entities, resolutions, strict=True)
            ]
        }
    )
    for entity, resolution in zip(plan.entities, resolutions, strict=True):
        if outcome := _entity_outcome(entity, resolution):
            return outcome

    terms = [
        bind_excluded(resolution) if entity.role == "exclude" else bind_term(resolution)
        for entity, resolution in zip(plan.entities, resolutions, strict=True)
    ]
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
        plan=plan,
        adjustments=adjustments,
    )


class _RejectedName(Exception):
    """The registry answered 400 to a search made of one entity's words: they are what it rejected."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.text = text


def _resolver_call(
    deps: ResolveDeps, entity: Entity, ctx: RequestLog, drug_match: DrugMatch, request: QueryRequest | None
) -> Callable[[], Awaitable[EntityResolution]]:
    may_reread = _source(request, entity) == "question"

    async def resolve() -> EntityResolution:
        try:
            return await deps.resolver.resolve(
                entity.kind, entity.value, ctx, drug_match=drug_match, may_reread=may_reread
            )
        except UpstreamRejectedQuery as error:
            # Every search here is the entity's words and nothing else, so the words are the cause. Words
            # the registry reads as an operator are a fault of the question, not of the service.
            raise _RejectedName(entity.value) from error

    return resolve


def _probe_call(deps: ResolveDeps, scope: Scope, ctx: RequestLog) -> Callable[[], Awaitable[int]]:
    return lambda: deps.ctgov.count(scope.params(), ctx, origin="probe")


def _counter(client: Registry, params: Params, ctx: RequestLog) -> Callable[[], Awaitable[int]]:
    return lambda: client.count(params, ctx, origin="resolution")


def _reading_note(resolution: EntityResolution) -> str:
    """Why a free-text name was searched as a drug, a condition or a term, in the registry's own counts."""
    if resolution.strict_name_matches is None or resolution.condition_name_matches is None:
        return ""
    named, listed = resolution.strict_name_matches, resolution.condition_name_matches
    text = resolution.text
    if resolution.kind == "term":
        return (
            f"'{text}' is neither clearly a drug nor a condition ({named:,} trials have it in an "
            f"intervention "
            f"name, {listed:,} in a condition), so it is searched anywhere in the record. "
        )
    changed = (
        f" (the plan called it a {resolution.planned_kind})"
        if resolution.kind != resolution.planned_kind
        else ""
    )
    return (
        f"Read '{text}' as a {resolution.kind}{changed}: {named:,} trials have it in an intervention "
        f"name and "
        f"{listed:,} in a condition. "
    )


def _kind_message(resolution: EntityResolution) -> str:
    return (
        f"'{resolution.text}' was read as a {resolution.kind}, not as the {resolution.planned_kind} the plan "
        f"named: {resolution.strict_name_matches or 0:,} trials have it in an intervention name and "
        f"{resolution.condition_name_matches or 0:,} in a condition."
    )


def _search_note(resolution: EntityResolution, count: int) -> str:
    text = resolution.text
    reading = _reading_note(resolution)
    match resolution.definition:
        case "intervention_search":
            return (
                f"{reading}Matched '{text}' with the registry's intervention search (names, other names, arm "
                f"labels, titles, descriptions and MeSH terms, with synonyms): {count:,} trials. "
                f"{resolution.strict_name_matches or 0:,} of them name it as an intervention."
            )
        case "condition_search":
            return (
                f"{reading}Matched '{text}' with the registry's condition search "
                f"(conditions, titles, keywords and MeSH terms, with synonyms): {count:,} trials."
            )
        case "lead_sponsor_search":
            return (
                f"Matched '{text}' in the lead sponsor name. This is a text search, so related "
                "organisations that mention the name are included."
            )
        case _:
            return f"{reading}Matched '{text}' anywhere in the record: {count:,} trials."


def _source(request: QueryRequest | None, entity: Entity) -> str:
    if request is None:
        return "plan"
    given: list[str] = list(getattr(request, _REQUEST_FIELD_OF[entity.kind]) or [])
    if request.compare is not None:
        given.extend(request.compare.values)
    if request.exclude is not None:
        given.extend(getattr(request.exclude, _REQUEST_FIELD_OF[entity.kind]) or [])
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
        notes.extend(_name_notes(resolution))
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


def _name_notes(resolution: EntityResolution) -> list[Note]:
    """Warnings about how well the wording matches what the registry itself holds under that name."""
    text, count = resolution.text, resolution.trials_matched
    notes: list[Note] = []
    named = resolution.strict_name_matches
    is_search = resolution.kind == "drug" and resolution.definition == "intervention_search"
    if is_search and count and named is not None and named < _NAME_SHARE_FLOOR * count:
        notes.append(
            Note(
                code="drug_name_barely_matches",
                message=f"'{text}' is in the intervention names of only {named:,} of the {count:,} trials "
                "that "
                "the intervention search matched; the rest mention it only in titles, descriptions, arm "
                "labels or MeSH terms. If it names a group of drugs rather than one drug, the trials of "
                "the individual drugs that never mention the group are not counted, so every figure here "
                "and the counts per drug in particular can be far too low. Set drug_match to "
                "'name_only' to count only the trials that name it as an intervention.",
            )
        )
    if (term := resolution.registry_term) is not None:
        notes.append(
            Note(
                code="wording_narrower_than_registry_term",
                message=f"'{text}' matched {count:,} trials, but the registry's own term for those trials, "
                f"'{term.term}', covers {term.trials_matched:,}. A synonym or the registry's own wording "
                "may give a different count.",
            )
        )
    return notes


def _scopes(plan: QueryPlan, entities: list[Entity], terms: list[BoundTerm]) -> tuple[Scope, ...]:
    shared = [term for entity, term in zip(entities, terms, strict=True) if entity.role == "filter"]
    compared = [term for entity, term in zip(entities, terms, strict=True) if entity.role == "compare"]
    excluded = tuple(term for entity, term in zip(entities, terms, strict=True) if entity.role == "exclude")
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
        Scope(
            id=f"s{position}",
            label=label,
            terms=group,
            enum_filters=enum_filters,
            date_range=date_range,
            excluded=excluded,
            excluded_statuses=tuple(plan.filters.exclude_statuses),
        )
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
    relaxed.extend(
        (
            f"leaving out '{term.text}'",
            dataclasses.replace(scope, excluded=tuple(t for t in scope.excluded if t is not term)),
        )
        for term in scope.excluded
    )
    if scope.excluded_statuses:
        relaxed.append(("the excluded statuses", dataclasses.replace(scope, excluded_statuses=())))
    for key in scope.enum_filters:
        remaining = {k: v for k, v in scope.enum_filters.items() if k != key}
        relaxed.append((_FILTER_NAME_OF[key], dataclasses.replace(scope, enum_filters=remaining)))
    if scope.date_range is not None:
        relaxed.append(("the date range", dataclasses.replace(scope, date_range=None)))
    return relaxed
