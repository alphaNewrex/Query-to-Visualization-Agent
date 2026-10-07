"""The study record: an API record parsed once into a frozen `Study` that knows where its values came from.

Under a `fields` projection the registry marks a missing value in three ways: the key is absent, its
parent object is `{}`, or an item of a list is `{}`. A value is never null. The parser therefore reads
each leaf and never trusts a module to hold it, and it maps all three forms to "no value". An item of a
list keeps its index, because a projection preserves array positions: the path
`protocolSection.armsInterventionsModule.interventions[3].name` taken from a projected record is valid
for the full record, and that path is what a citation quotes.
"""

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Final

# Records are parsed from JSON, so a value is one of str, int, float, bool, list, dict or absent.
JsonObject = Mapping[str, object]

_PATH: Final = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*|\[\d+\])*")
_STEP: Final = re.compile(r"([A-Za-z_]\w*)|\[(\d+)\]")


@dataclass(frozen=True)
class StudyDate:
    """A `...DateStruct` of the record."""

    date: str  # "2015-03-06" or "2015-03"; the registry reads a month as its first day
    type: str | None  # "ACTUAL" or "ESTIMATED"; absent on old records


@dataclass(frozen=True)
class Intervention:
    index: int  # position in the record's `interventions` array
    type: str | None
    name: str | None
    other_names: tuple[str, ...]  # positions are those of the record's `otherNames` array
    arm_group_labels: tuple[str, ...]


@dataclass(frozen=True)
class Location:
    index: int  # position in the record's `locations` array
    country: str | None


@dataclass(frozen=True)
class Study:
    """One trial. Every field except `nct_id` can be missing: None, or an empty tuple for a list.

    A withheld study has no sponsor class, design, conditions, interventions or start date, and the
    sponsor name `[Redacted]`; all of that is kept as it came.
    """

    nct_id: str
    brief_title: str | None
    overall_status: str | None
    study_type: str | None
    phases: tuple[str, ...]
    start_date: StudyDate | None
    primary_completion_date: StudyDate | None
    completion_date: StudyDate | None
    first_post_date: StudyDate | None
    enrollment_count: int | None
    enrollment_type: str | None
    lead_sponsor_name: str | None
    lead_sponsor_class: str | None
    conditions: tuple[str, ...]
    interventions: tuple[Intervention, ...]
    intervention_mesh_terms: tuple[str, ...]
    locations: tuple[Location, ...]
    sex: str | None
    std_ages: tuple[str, ...]
    allocation: str | None
    masking: str | None
    primary_purpose: str | None
    has_results: bool | None
    # The record as the API sent it, which a citation's path is resolved against.
    raw: JsonObject = field(repr=False, compare=False)

    def value_at(self, path: str) -> object | None:
        """The JSON value at a path in the API's own notation, `protocolSection.designModule.phases[0]`.

        None when the record has no value there, which is also what a citation with a null excerpt says.
        """
        if not _PATH.fullmatch(path):
            raise ValueError(f"Not a JSON path: {path!r}.")
        node: object = self.raw
        for key, index in _STEP.findall(path):
            if key:
                node = node.get(key) if isinstance(node, Mapping) else None
            else:
                node = node[int(index)] if isinstance(node, list) and int(index) < len(node) else None
            if node is None:
                return None
        return node


def parse_study(record: JsonObject) -> Study:
    """Parse one record of the API's `studies` array.

    Raises ValueError when it has no NCT ID: nothing can be cited without one.
    """
    protocol = _dig(record, "protocolSection")
    status = _dig(protocol, "statusModule")
    design = _dig(protocol, "designModule")
    sponsor = _dig(protocol, "sponsorCollaboratorsModule", "leadSponsor")
    eligibility = _dig(protocol, "eligibilityModule")

    nct_id = _text(_dig(protocol, "identificationModule", "nctId"))
    if nct_id is None:
        raise ValueError("A study record has no NCT ID.")
    return Study(
        nct_id=nct_id,
        brief_title=_text(_dig(protocol, "identificationModule", "briefTitle")),
        overall_status=_text(_dig(status, "overallStatus")),
        study_type=_text(_dig(design, "studyType")),
        phases=_strings(_dig(design, "phases")),
        start_date=_date(_dig(status, "startDateStruct")),
        primary_completion_date=_date(_dig(status, "primaryCompletionDateStruct")),
        completion_date=_date(_dig(status, "completionDateStruct")),
        first_post_date=_date(_dig(status, "studyFirstPostDateStruct")),
        enrollment_count=_whole_number(_dig(design, "enrollmentInfo", "count")),
        enrollment_type=_text(_dig(design, "enrollmentInfo", "type")),
        lead_sponsor_name=_text(_dig(sponsor, "name")),
        lead_sponsor_class=_text(_dig(sponsor, "class")),
        conditions=_strings(_dig(protocol, "conditionsModule", "conditions")),
        interventions=tuple(
            Intervention(
                index=index,
                type=_text(item.get("type")),
                name=_text(item.get("name")),
                other_names=_strings(item.get("otherNames")),
                arm_group_labels=_strings(item.get("armGroupLabels")),
            )
            for index, item in _items(_dig(protocol, "armsInterventionsModule", "interventions"))
        ),
        intervention_mesh_terms=tuple(
            term
            for _, mesh in _items(_dig(record, "derivedSection", "interventionBrowseModule", "meshes"))
            if (term := _text(mesh.get("term"))) is not None
        ),
        locations=tuple(
            Location(index=index, country=_text(item.get("country")))
            for index, item in _items(_dig(protocol, "contactsLocationsModule", "locations"))
        ),
        sex=_text(_dig(eligibility, "sex")),
        std_ages=_strings(_dig(eligibility, "stdAges")),
        allocation=_text(_dig(design, "designInfo", "allocation")),
        masking=_text(_dig(design, "designInfo", "maskingInfo", "masking")),
        primary_purpose=_text(_dig(design, "designInfo", "primaryPurpose")),
        has_results=_flag(record.get("hasResults")),
        raw=record,
    )


def _dig(node: object, *keys: str) -> object | None:
    """The value under a chain of keys; None where the chain breaks or a parent is not an object."""
    for key in keys:
        if not isinstance(node, Mapping):
            return None
        node = node.get(key)
    return node


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _whole_number(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _flag(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _strings(value: object) -> tuple[str, ...]:
    """The strings of a list; anything else, an `{}` among them, is no value."""
    return tuple(item for item in value if isinstance(item, str)) if isinstance(value, list) else ()


def _date(value: object) -> StudyDate | None:
    day = _text(_dig(value, "date"))
    return None if day is None else StudyDate(date=day, type=_text(_dig(value, "type")))


def _items(value: object) -> Iterator[tuple[int, JsonObject]]:
    """The objects of a list with their positions, leaving out the `{}` that stand for no value."""
    if isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, Mapping) and item:
                yield index, item
