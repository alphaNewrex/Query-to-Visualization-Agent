"""Per-trial rows for scatter plots and trial lists: the same records, no grouping.

Each numeric field returns its number together with the evidence it was read from, so that every point
carries its own citation. A trial without a value for a plotted field is left out and counted by reason.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Final

from ctviz.catalog.fields import CATALOG, BoundDimension, Evidence, Value
from ctviz.contract.plan import NumericField, SortField
from ctviz.contract.response import Note, StrategyName
from ctviz.ctgov.params import Scope
from ctviz.ctgov.study import Location, Study, StudyDate
from ctviz.engine.frame import Exclusion
from ctviz.engine.lower import EnginePlan, ListRows, PointRows

_PROTOCOL: Final = "protocolSection"
_STATUS: Final = "protocolSection.statusModule"
_DESIGN: Final = "protocolSection.designModule"
_LOCATIONS: Final = "protocolSection.contactsLocationsModule.locations"
_SORT_DATES: Final = {
    "start_date": ("start_date", f"{_STATUS}.startDateStruct.date"),
    "completion_date": ("completion_date", f"{_STATUS}.completionDateStruct.date"),
    "first_posted_date": ("first_post_date", f"{_STATUS}.studyFirstPostDateStruct.date"),
}


# The cells a row of the trial table shows after the NCT ID, the title and the sort column.
LISTED_COLUMNS: Final = ("phase", "overall_status", "start_date", "enrollment", "lead_sponsor")
# The trials a number cannot be read for, by the field it is taken of.
NO_MEASURE: Final = {
    "enrollment": ("no_enrollment", "No enrollment count on record"),
    "duration_months": (
        "no_duration_months",
        "No start and completion date to measure a duration from, or a completion before the start",
    ),
    "site_count": ("no_site_count", "No site listed"),
}


@dataclass(frozen=True)
class Reading:
    """A trial's number for one field, where it was read, and how the record states it.

    `basis` has one word for each value the number rests on, so that a response can say how many are
    planned and how many actual: `start_actual`, `end_estimated`, `enrollment_zero`.
    """

    value: float
    evidence: tuple[Evidence, ...]
    basis: tuple[str, ...] = ()


@dataclass(frozen=True)
class TrialRow:
    """One trial: its numbers, its colour value, and the evidence for what is shown."""

    study: Study
    x: float | None  # None for a trial list
    y: float | None
    color: Value | None
    evidence: tuple[Evidence, ...]


@dataclass
class RowsResult:
    """The rows of one scope, with the counters that make its counts reconcile."""

    scope: Scope
    rows: list[TrialRow]
    seen: int  # trials read
    matched: int  # totalCount of the scope
    excluded: dict[str, Exclusion] = field(default_factory=dict)  # by reason
    strategy: StrategyName = "walk"
    warnings: list[Note] = field(default_factory=list)

    def exclude(self, reason: str, message: str, count: int = 1) -> None:
        self.excluded.setdefault(reason, Exclusion(message)).count += count


def trial_rows(studies: Iterable[Study], plan: EnginePlan, scope: Scope) -> RowsResult:
    """One row per trial that has every value the plan needs."""
    result = RowsResult(scope=scope, rows=[], seen=0, matched=0)
    for study in studies:
        result.seen += 1
        match plan.rows:
            case PointRows() as spec:
                row = _point(study, spec, result)
            case ListRows(sort_by=sort_by):
                row = TrialRow(study, None, None, None, _listed_evidence(study, sort_by))
            case _:
                raise ValueError("The plan has no per-trial rows.")
        if row is not None:
            result.rows.append(row)
    result.matched = result.seen
    return result


def _point(study: Study, spec: PointRows, result: RowsResult) -> TrialRow | None:
    x, y = reading(study, spec.x), reading(study, spec.y)
    if x is None or y is None:
        result.exclude(*NO_MEASURE[spec.x if x is None else spec.y])
        return None
    color = _color(study, spec)
    evidence = dict.fromkeys((*x.evidence, *y.evidence, *(color.evidence if color else ())))
    return TrialRow(study, x.value, y.value, color, tuple(evidence))


def _color(study: Study, spec: PointRows) -> Value | None:
    if spec.color is None:
        return None
    values = spec.color.spec.extract(study, None, spec.color)
    if values:
        return values[0]
    missing = spec.color.spec.missing
    return None if missing is None else Value(missing.key, missing.label, ())


def reading(study: Study, name: NumericField, sites: Sequence[Location] | None = None) -> Reading | None:
    """A trial's number for a field and where it was read; None when the record does not give it.

    `sites` narrows a count of sites to some of the trial's own: the ones in one country or state. A trial
    that lists no site has no site count, for a plot and for a statistic alike.
    """
    if name == "enrollment":
        return _enrollment(study)
    if name == "site_count":
        if not study.locations:
            return None
        counted = study.locations if sites is None else sites
        return Reading(float(len(counted)), _site_evidence(study, counted))
    if study.start_date is None or study.completion_date is None:
        return None
    months = _months_between(study.start_date, study.completion_date)
    if months is None:
        return None
    evidence = (
        *_dated(f"{_STATUS}.startDateStruct", study.start_date),
        *_dated(f"{_STATUS}.completionDateStruct", study.completion_date),
    )
    basis = (f"start_{_how(study.start_date.type)}", f"end_{_how(study.completion_date.type)}")
    return Reading(months, evidence, basis)


def _enrollment(study: Study) -> Reading | None:
    count = study.enrollment_count
    if count is None:
        return None
    info = f"{_DESIGN}.enrollmentInfo"
    evidence = [Evidence(f"{info}.count", str(count))]
    if study.enrollment_type is not None:
        evidence.append(Evidence(f"{info}.type", study.enrollment_type))
    basis = [f"enrollment_{_how(study.enrollment_type)}"]
    if count == 0:
        basis.append("enrollment_zero")  # a withdrawn trial reports none
    return Reading(float(count), tuple(evidence), tuple(basis))


def _how(kind: str | None) -> str:
    """How a record states a value: `actual`, `estimated`, or `unstated` where it does not say."""
    return {"ACTUAL": "actual", "ESTIMATED": "estimated"}.get(kind or "", "unstated")


def _dated(path: str, found: StudyDate) -> tuple[Evidence, ...]:
    date_evidence = Evidence(f"{path}.date", found.date)
    return (
        (date_evidence, Evidence(f"{path}.type", found.type)) if found.type is not None else (date_evidence,)
    )


def _site_evidence(study: Study, counted: Sequence[Location]) -> tuple[Evidence, ...]:
    """Evidence that proves a count of sites: where the list of locations ends and where the counted ones lie.

    The registry's list of locations has no gaps, so the last site (`locations[n-1]`) and the absence of
    the position after it say that the list holds exactly n sites. A count within one country or state also
    quotes the first and the last site that were counted; the sites between them are not each quoted. A
    site with no country is quoted by the absence of its country.
    """
    last = study.locations[-1]
    found: list[Evidence] = []
    if len(counted) != len(study.locations):
        found.extend(_site(site) for site in (counted[0], counted[-1]))
    found.append(_site(last))
    after = f"{_LOCATIONS}[{last.index + 1}]"
    if study.value_at(after) is None:
        found.append(Evidence(after, None))
    return tuple(dict.fromkeys(found))


def _site(location: Location) -> Evidence:
    return Evidence(f"{_LOCATIONS}[{location.index}].country", location.country)


def _months_between(start: StudyDate | None, end: StudyDate | None) -> float | None:
    """The time from a start to an end in months, a `YYYY-MM` date read as the first of its month.

    The whole calendar months from the start that fit before the end, plus the rest as a part of the next
    month: so a duration between two month-only dates is a whole number, and a day makes a difference
    (1 January to 1 February is 1; 31 January to 1 February is 1 of the 29 days to the end of that month).
    A trial that ends before it starts has no duration worth plotting.
    """
    if start is None or end is None:
        return None
    first, last = _day(start.date), _day(end.date)
    if last < first:
        return None
    whole = (last.year - first.year) * 12 + last.month - first.month
    if _add_months(first, whole) > last:
        whole -= 1
    begun = _add_months(first, whole)
    span = (_add_months(first, whole + 1) - begun).days
    return round(whole + (last - begun).days / span, 3)


def _add_months(day: date, months: int) -> date:
    """The same day of a later month, or the last day of a month that is too short for it."""
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    month += 1
    following = date(year + (month == 12), month % 12 + 1, 1)
    last_day = (following - date(year, month, 1)).days
    return date(year, month, min(day.day, last_day))


def _day(text: str) -> date:
    return date.fromisoformat(text if len(text) == 10 else f"{text}-01")


def _listed_evidence(study: Study, sort_by: SortField) -> tuple[Evidence, ...]:
    """One quoted value for each cell a row of the trial table shows, an absent one quoted as absent."""
    columns = ("nct_id", "title", sort_by, *(c for c in LISTED_COLUMNS if c != sort_by))
    found: list[Evidence] = []
    for column in columns:
        found.extend(_cell_evidence(study, column))
    return tuple(dict.fromkeys(found))


def _cell_evidence(study: Study, column: str) -> tuple[Evidence, ...]:
    ident, status, sponsor = (
        f"{_PROTOCOL}.identificationModule",
        _STATUS,
        f"{_PROTOCOL}.sponsorCollaboratorsModule",
    )
    match column:
        case "nct_id":
            return (Evidence(f"{ident}.nctId", study.nct_id),)
        case "title":
            return (Evidence(f"{ident}.briefTitle", study.brief_title),)
        case "phase":
            spec = CATALOG["phase"]
            return spec.extract(study, None, BoundDimension(spec, None, "axis"))[0].evidence
        case "overall_status":
            return (Evidence(f"{status}.overallStatus", study.overall_status),)
        case "enrollment":
            found = _enrollment(study)
            return (
                found.evidence if found is not None else (Evidence(f"{_DESIGN}.enrollmentInfo.count", None),)
            )
        case "lead_sponsor":
            return (Evidence(f"{sponsor}.leadSponsor.name", study.lead_sponsor_name),)
        case _:
            attribute, path = _SORT_DATES[column]
            value: StudyDate | None = getattr(study, attribute)
            return (Evidence(path, None if value is None else value.date),)
