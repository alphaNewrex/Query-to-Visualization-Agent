"""One small pure function per field: a study to the values it contributes, each with its evidence.

Every function has the `FieldSpec.extract` signature. A value's evidence is the path in the record
where it was read, in the API's own notation, and the exact text found there. A trial without a value
gets none, except where the field has a "Not provided" bucket: then the extractor returns that bucket
as the value, and its evidence is the path with an excerpt of None (the absence is the evidence).
"""

from collections.abc import Callable, Sequence

from ctviz.catalog import periods, vocab
from ctviz.catalog.conditions import ConditionLabels, fold
from ctviz.catalog.drugs import DrugNormalizer
from ctviz.catalog.fields import BoundDimension, Evidence, FieldContext, Value
from ctviz.catalog.states import StateScope, fold_state
from ctviz.ctgov.study import Location, Study, StudyDate

Extractor = Callable[[Study, FieldContext | None, BoundDimension], Sequence[Value]]

PROTOCOL = "protocolSection"
STATUS = f"{PROTOCOL}.statusModule"
DESIGN = f"{PROTOCOL}.designModule"
SPONSOR = f"{PROTOCOL}.sponsorCollaboratorsModule.leadSponsor"
ELIGIBILITY = f"{PROTOCOL}.eligibilityModule"
INTERVENTIONS = f"{PROTOCOL}.armsInterventionsModule.interventions"
REDACTED_SPONSOR = "[Redacted]"


def _token_value(vocabulary: vocab.Vocabulary, token: str, evidence: Evidence) -> Value:
    return Value(token, vocabulary.labels.get(token, token), (evidence,))


def _not_provided(path: str) -> Value:
    return Value(vocab.NOT_PROVIDED_KEY, vocab.NOT_PROVIDED_LABEL, (Evidence(path, None),))


def single_token(
    vocabulary: vocab.Vocabulary, read: Callable[[Study], str | None], path: str, *, has_missing_bucket: bool
) -> Extractor:
    """A closed field with one token per trial; one without goes to "Not provided" where the field has it."""

    def extract(study: Study, _context: FieldContext | None, _dimension: BoundDimension) -> Sequence[Value]:
        token = read(study)
        if token is not None:
            return [_token_value(vocabulary, token, Evidence(path, token))]
        return [_not_provided(path)] if has_missing_bucket else []

    return extract


def phase(study: Study, _context: FieldContext | None, _dimension: BoundDimension) -> Sequence[Value]:
    """The one exclusive bucket of a trial's phase array, which holds one phase, two, or none."""
    path = f"{DESIGN}.phases"
    evidence = tuple(Evidence(f"{path}[{index}]", token) for index, token in enumerate(study.phases))
    if not evidence:
        return [Value(vocab.NO_PHASE_KEY, vocab.PHASE_LABELS[vocab.NO_PHASE_KEY], (Evidence(path, None),))]
    key = vocab.PHASE_COMBINATIONS.get(frozenset(study.phases), vocab.OTHER_PHASE_KEY)
    return [Value(key, vocab.PHASE_LABELS.get(key, vocab.OTHER_PHASE_LABEL), evidence)]


def intervention_type(
    study: Study, _context: FieldContext | None, _dimension: BoundDimension
) -> Sequence[Value]:
    """Each distinct intervention type once, read at the first intervention that has it."""
    vocabulary = vocab.intervention_type()
    found: dict[str, Value] = {}
    for item in study.interventions:
        if item.type is not None and item.type not in found:
            evidence = Evidence(f"{INTERVENTIONS}[{item.index}].type", item.type)
            found[item.type] = _token_value(vocabulary, item.type, evidence)
    return list(found.values())


def age_group(study: Study, _context: FieldContext | None, _dimension: BoundDimension) -> Sequence[Value]:
    """Each listed age group once."""
    vocabulary = vocab.age_group()
    path = f"{ELIGIBILITY}.stdAges"
    return [
        _token_value(vocabulary, token, Evidence(f"{path}[{index}]", token))
        for index, token in enumerate(dict.fromkeys(study.std_ages))
    ]


def has_results(study: Study, _context: FieldContext | None, _dimension: BoundDimension) -> Sequence[Value]:
    if study.has_results is None:
        return []
    token = "true" if study.has_results else "false"
    return [_token_value(vocab.has_results(), token, Evidence("hasResults", token))]


def date_field(read: Callable[[Study], StudyDate | None], path: str) -> Extractor:
    """The period, of the axis' unit, that holds a date field; a trial without the date has no value."""

    def extract(study: Study, _context: FieldContext | None, dimension: BoundDimension) -> Sequence[Value]:
        found = read(study)
        label = None if found is None else periods.period_of(found.date, dimension.time_unit or "year")
        if found is None or label is None:
            return []
        return [Value(label, label, (Evidence(path, found.date),))]

    return extract


def enrollment(study: Study, _context: FieldContext | None, _dimension: BoundDimension) -> Sequence[Value]:
    count = study.enrollment_count
    if count is None:
        return []
    bucket = next((b for b in vocab.ENROLLMENT_BINS if b.holds(count)), None)
    if bucket is None:
        return []
    return [Value(bucket.label, bucket.label, (Evidence(f"{DESIGN}.enrollmentInfo.count", str(count)),))]


def country(study: Study, _context: FieldContext | None, _dimension: BoundDimension) -> Sequence[Value]:
    """Each distinct site country once, however many sites it has, read at its first site."""
    found: dict[str, Value] = {}
    for location in study.locations:
        if location.country is None:
            continue
        name = location.country.strip()
        if name and name not in found:
            path = f"{PROTOCOL}.contactsLocationsModule.locations[{location.index}].country"
            found[name] = Value(name, name, (Evidence(path, location.country),))
    return list(found.values())


def country_sites(study: Study, _context: FieldContext | None, key: str) -> Sequence[Location]:
    """The sites in one country, the ones `country` counts that country's trial for."""
    return [location for location in study.locations if (location.country or "").strip() == key]


def state_sites(study: Study, context: FieldContext | None, key: str) -> Sequence[Location]:
    """The sites in one state, within the countries the question names (every country when it names none)."""
    scope = context if isinstance(context, StateScope) else StateScope(())
    return [
        location
        for location in study.locations
        if location.state is not None
        and fold_state(location.state) == key
        and scope.includes(location.country)
    ]


def state(study: Study, context: FieldContext | None, _dimension: BoundDimension) -> Sequence[Value]:
    """Each distinct state with a site once, however many sites it has there, read at its first site.

    Only sites in the countries the question names count (every site when it names none). A site's
    evidence is its state and its country, both quoted from the record. Spellings that differ only in
    case, accents or punctuation are one state.
    """
    scope = context if isinstance(context, StateScope) else StateScope(())
    found: dict[str, Value] = {}
    for location in study.locations:
        if location.state is None or not scope.includes(location.country):
            continue
        key = fold_state(location.state)
        if key and key not in found:
            base = f"{PROTOCOL}.contactsLocationsModule.locations[{location.index}]"
            evidence = [Evidence(f"{base}.state", location.state)]
            if location.country is not None:
                evidence.append(Evidence(f"{base}.country", location.country))
            found[key] = Value(key, scope.labels.get(key, location.state.strip()), tuple(evidence))
    return list(found.values())


def sponsor(study: Study, _context: FieldContext | None, _dimension: BoundDimension) -> Sequence[Value]:
    """The lead sponsor's name as registered; a withheld study's `[Redacted]` is not a sponsor."""
    name = study.lead_sponsor_name
    if name is None or name.strip() in {"", REDACTED_SPONSOR}:
        return []
    return [Value(name, name, (Evidence(f"{SPONSOR}.name", name),))]


def condition(study: Study, context: FieldContext | None, _dimension: BoundDimension) -> Sequence[Value]:
    """Each listed condition once, folded and labelled by its commonest spelling where the set is known."""
    labels = context.labels if isinstance(context, ConditionLabels) else {}
    found: dict[str, Value] = {}
    for index, raw in enumerate(study.conditions):
        key = fold(raw)
        if key and key not in found:
            evidence = Evidence(f"{PROTOCOL}.conditionsModule.conditions[{index}]", raw)
            found[key] = Value(key, labels.get(key, raw), (evidence,))
    return list(found.values())


def drug(study: Study, context: FieldContext | None, _dimension: BoundDimension) -> Sequence[Value]:
    if not isinstance(context, DrugNormalizer):
        raise TypeError("The drug field needs the DrugNormalizer fitted by its `prepare` step.")
    return context.values(study)
