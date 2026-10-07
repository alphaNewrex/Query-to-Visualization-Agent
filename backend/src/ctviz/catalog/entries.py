"""The catalogue entries: one `FieldSpec` per dimension, in the order of the plan's `DimensionKey`."""

from collections.abc import Callable, Sequence
from typing import Final

from ctviz.catalog import extractors, periods, vocab
from ctviz.catalog.conditions import ConditionLabels
from ctviz.catalog.countries import load_country_table
from ctviz.catalog.drugs import DRUG_TYPES, DrugNormalizer
from ctviz.catalog.extractors import Extractor
from ctviz.catalog.fields import BoundDimension, Bucket, FieldSpec, Window
from ctviz.ctgov.essie import Expr, any_of, area, missing, not_, range_
from ctviz.ctgov.study import Study, StudyDate

_Buckets = Callable[[BoundDimension, Window | None], Sequence[Bucket]]


def _fixed(buckets: Sequence[Bucket]) -> _Buckets:
    """A closed list of buckets that does not depend on the question."""
    return lambda _dimension, _window: buckets


def _present(piece: str) -> Expr:
    return not_(missing(piece))


def _closed(
    key: str,
    title: str,
    vocabulary: vocab.Vocabulary,
    read: Callable[[Study], str | None],
    path: str,
    *,
    has_missing_bucket: bool,
    pieces: tuple[str, ...] | None = None,
) -> FieldSpec:
    """A closed dimension with one token per trial, whose trials without one go to "Not provided".

    That bucket is listed with the others, so that a fan-out counts it like any other and the buckets
    partition the trials.
    """
    return FieldSpec(
        key=key,
        title=title,
        kind="category",
        pieces=pieces or (vocabulary.piece,),
        extract=extractors.single_token(vocabulary, read, path, has_missing_bucket=has_missing_bucket),
        is_exclusive=True,
        is_ordinal=False,
        buckets=_fixed(vocabulary.buckets(with_missing=has_missing_bucket)),
        missing=vocabulary.not_provided if has_missing_bucket else None,
    )


def _multi_valued(
    key: str, title: str, vocabulary: vocab.Vocabulary, extract: Extractor, note: str
) -> FieldSpec:
    """A closed dimension where a trial can hold several tokens, so its values overlap."""
    buckets = vocabulary.buckets()
    return FieldSpec(
        key=key,
        title=title,
        kind="category",
        pieces=(vocabulary.piece,),
        extract=extract,
        is_exclusive=False,
        is_ordinal=False,
        buckets=_fixed(buckets),
        presence=_present(vocabulary.piece),
        notes=(note,),
    )


def _date_buckets(piece: str) -> _Buckets:
    def buckets(_dimension: BoundDimension, window: Window | None) -> Sequence[Bucket]:
        if window is None:
            raise ValueError("A date axis needs a window of periods.")
        return [
            Bucket(label, label, range_(piece, *periods.period_range(label)))
            for label in periods.window_labels(window)
        ]

    return buckets


def _date_field(
    key: str,
    title: str,
    piece: str,
    type_piece: str | None,
    read: Callable[[Study], StudyDate | None],
    path: str,
    note: str,
    *,
    can_be_missing: bool = True,
) -> FieldSpec:
    return FieldSpec(
        key=key,
        title=title,
        kind="date",
        pieces=(piece, type_piece) if type_piece else (piece,),
        extract=extractors.date_field(read, path),
        is_exclusive=True,
        is_ordinal=True,
        buckets=_date_buckets(piece),
        presence=_present(piece) if can_be_missing else None,
        notes=(note,),
    )


def _country_bucket(name: str) -> Bucket | None:
    return Bucket(name, name, area("LocationCountry", name)) if name in load_country_table() else None


def _entries() -> list[FieldSpec]:
    status = f"{extractors.STATUS}"
    design_info = f"{extractors.DESIGN}.designInfo"
    enrollment_buckets = [bin_.bucket() for bin_ in vocab.ENROLLMENT_BINS]
    return [
        FieldSpec(
            key="phase",
            title="Phase",
            kind="category",
            pieces=("Phase", "StudyType"),
            extract=extractors.phase,
            is_exclusive=True,
            is_ordinal=True,
            buckets=_fixed(vocab.PHASE_BUCKETS),
            notes=(
                "Phases are nine exclusive groups: a trial listing Phase 1 and Phase 2 is counted once, "
                "as Phase 1/Phase 2.",
            ),
        ),
        _closed(
            "overall_status",
            "Overall status",
            vocab.overall_status(),
            lambda study: study.overall_status,
            f"{status}.overallStatus",
            has_missing_bucket=False,
        ),
        _closed(
            "study_type",
            "Study type",
            vocab.study_type(),
            lambda study: study.study_type,
            f"{extractors.DESIGN}.studyType",
            has_missing_bucket=True,
        ),
        _closed(
            "sponsor_class",
            "Sponsor class",
            vocab.sponsor_class(),
            lambda study: study.lead_sponsor_class,
            f"{extractors.SPONSOR}.class",
            has_missing_bucket=True,
            pieces=("LeadSponsorClass",),
        ),
        _multi_valued(
            "intervention_type",
            "Intervention type",
            vocab.intervention_type(),
            extractors.intervention_type,
            "A trial counts once for each intervention type it lists, so the groups can overlap.",
        ),
        _closed(
            "sex",
            "Sex",
            vocab.sex(),
            lambda study: study.sex,
            f"{extractors.ELIGIBILITY}.sex",
            has_missing_bucket=True,
        ),
        _multi_valued(
            "age_group",
            "Age group",
            vocab.age_group(),
            extractors.age_group,
            "A trial counts once for each age group it lists, so the groups can overlap.",
        ),
        _closed(
            "allocation",
            "Allocation",
            vocab.allocation(),
            lambda study: study.allocation,
            f"{design_info}.allocation",
            has_missing_bucket=True,
        ),
        _closed(
            "masking",
            "Masking",
            vocab.masking(),
            lambda study: study.masking,
            f"{design_info}.maskingInfo.masking",
            has_missing_bucket=True,
        ),
        _closed(
            "primary_purpose",
            "Primary purpose",
            vocab.primary_purpose(),
            lambda study: study.primary_purpose,
            f"{design_info}.primaryPurpose",
            has_missing_bucket=True,
        ),
        FieldSpec(
            key="has_results",
            title="Results posted",
            kind="category",
            pieces=("HasResults",),
            extract=extractors.has_results,
            is_exclusive=True,
            is_ordinal=False,
            buckets=_fixed(vocab.has_results().buckets()),
        ),
        FieldSpec(
            key="country",
            title="Country",
            kind="entity",
            pieces=("LocationCountry",),
            extract=extractors.country,
            is_exclusive=False,
            is_ordinal=False,
            bucket_for=_country_bucket,
            presence=_present("LocationCountry"),
            notes=(
                "A trial counts once for each country with a site, so the bars can overlap. "
                "Trials whose locations were removed after completion are not attributed to a country.",
            ),
        ),
        FieldSpec(
            key="sponsor",
            title="Sponsor",
            kind="entity",
            pieces=("LeadSponsorName", "LeadSponsorClass"),
            extract=extractors.sponsor,
            is_exclusive=True,
            is_ordinal=False,
            notes=(
                "Sponsors are the lead sponsor's name as registered; variants of one name are not merged.",
            ),
        ),
        FieldSpec(
            key="drug",
            title="Drug",
            kind="entity",
            pieces=(
                "InterventionType",
                "InterventionName",
                "InterventionOtherName",
                "InterventionArmGroupLabel",
                "InterventionMeshTerm",
            ),
            extract=extractors.drug,
            is_exclusive=False,
            is_ordinal=False,
            presence=any_of("InterventionType", DRUG_TYPES),
            prepare=DrugNormalizer.fit,
            notes=(
                "Drug names are normalised from free text: doses and forms are removed, placebos and "
                "non-drug care are left out, and a combination is split only when each part is a known drug.",
            ),
        ),
        FieldSpec(
            key="condition",
            title="Condition",
            kind="entity",
            pieces=("Condition",),
            extract=extractors.condition,
            is_exclusive=False,
            is_ordinal=False,
            presence=_present("Condition"),
            prepare=ConditionLabels.fit,
            notes=("Conditions are free text, case-folded; spellings of one condition can stay separate.",),
        ),
        _date_field(
            "start_date",
            "Start date",
            "StartDate",
            "StartDateType",
            lambda study: study.start_date,
            f"{status}.startDateStruct.date",
            "Trials are placed by start date; estimated dates are included, and withdrawn trials keep "
            "their planned start date.",
        ),
        _date_field(
            "primary_completion_date",
            "Primary completion date",
            "PrimaryCompletionDate",
            "PrimaryCompletionDateType",
            lambda study: study.primary_completion_date,
            f"{status}.primaryCompletionDateStruct.date",
            "Trials are placed by primary completion date; estimated dates are included.",
        ),
        _date_field(
            "completion_date",
            "Completion date",
            "CompletionDate",
            "CompletionDateType",
            lambda study: study.completion_date,
            f"{status}.completionDateStruct.date",
            "Trials are placed by completion date; estimated dates are included.",
        ),
        _date_field(
            "first_posted_date",
            "First posted date",
            "StudyFirstPostDate",
            None,
            lambda study: study.first_post_date,
            f"{status}.studyFirstPostDateStruct.date",
            "Trials are placed by the date they were first posted to the registry.",
            can_be_missing=False,
        ),
        FieldSpec(
            key="enrollment",
            title="Enrollment",
            kind="number",
            pieces=("EnrollmentCount", "EnrollmentType"),
            extract=extractors.enrollment,
            is_exclusive=True,
            is_ordinal=True,
            buckets=_fixed(enrollment_buckets),
            presence=_present("EnrollmentCount"),
            notes=("Enrollment counts, estimated and actual, are grouped into fixed bins.",),
        ),
    ]


ENTRIES: Final = {spec.key: spec for spec in _entries()}
