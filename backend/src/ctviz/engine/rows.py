"""Per-trial rows for scatter plots and trial lists: the same records, no grouping.

Each numeric field returns its number together with the evidence it was read from, so that every point
carries its own citation. A trial without a value for a plotted field is left out and counted by reason.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Final

from ctviz.catalog.fields import Evidence, Value
from ctviz.contract.plan import NumericField, SortField
from ctviz.contract.response import Note, StrategyName
from ctviz.ctgov.params import Scope
from ctviz.ctgov.study import Study, StudyDate
from ctviz.engine.frame import Exclusion, SubsetInfo
from ctviz.engine.lower import EnginePlan, ListRows, PointRows

_STATUS: Final = "protocolSection.statusModule"
_DESIGN: Final = "protocolSection.designModule"
_LOCATIONS: Final = "protocolSection.contactsLocationsModule.locations"
_SORT_DATES: Final = {
    "start_date": ("start_date", f"{_STATUS}.startDateStruct.date"),
    "completion_date": ("completion_date", f"{_STATUS}.completionDateStruct.date"),
    "first_posted_date": ("first_post_date", f"{_STATUS}.studyFirstPostDateStruct.date"),
}


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
    subset: SubsetInfo | None = None
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
                row = TrialRow(study, None, None, None, _sort_evidence(study, sort_by))
            case _:
                raise ValueError("The plan has no per-trial rows.")
        if row is not None:
            result.rows.append(row)
    result.matched = result.seen
    return result


def _point(study: Study, spec: PointRows, result: RowsResult) -> TrialRow | None:
    x, y = _number(study, spec.x), _number(study, spec.y)
    if x is None or y is None:
        name = spec.x if x is None else spec.y
        result.exclude(f"no_{name}", f"No {name.replace('_', ' ')} on record")
        return None
    color = _color(study, spec)
    return TrialRow(study, x[0], y[0], color, (*x[1], *y[1], *(color.evidence if color else ())))


def _color(study: Study, spec: PointRows) -> Value | None:
    if spec.color is None:
        return None
    values = spec.color.spec.extract(study, None, spec.color)
    if values:
        return values[0]
    missing = spec.color.spec.missing
    return None if missing is None else Value(missing.key, missing.label, ())


def _number(study: Study, name: NumericField) -> tuple[float, tuple[Evidence, ...]] | None:
    """The value of a numeric field and where it was read; None when the record does not give it."""
    if name == "enrollment":
        count = study.enrollment_count
        if count is None:
            return None
        return float(count), (Evidence(f"{_DESIGN}.enrollmentInfo.count", str(count)),)
    if name == "site_count":
        return float(len(study.locations)), _site_evidence(study)
    months = _months_between(study.start_date, study.completion_date)
    if months is None or study.start_date is None or study.completion_date is None:
        return None
    return float(months), (
        Evidence(f"{_STATUS}.startDateStruct.date", study.start_date.date),
        Evidence(f"{_STATUS}.completionDateStruct.date", study.completion_date.date),
    )


def numeric_value(study: Study, name: NumericField) -> tuple[float, tuple[Evidence, ...]] | None:
    """The value of a numeric field for a statistic; a trial that lists no site has no site count."""
    if name == "site_count" and not study.locations:
        return None
    return _number(study, name)


def _site_evidence(study: Study) -> tuple[Evidence, ...]:
    for location in study.locations:
        if location.country is not None:
            return (Evidence(f"{_LOCATIONS}[{location.index}].country", location.country),)
    return (Evidence(_LOCATIONS, None),)  # no site listed: the absence is the evidence


def _months_between(start: StudyDate | None, end: StudyDate | None) -> int | None:
    """Whole months from a start to an end, a `YYYY-MM` value read as the first of its month.

    A trial that ends before it starts has no duration worth plotting.
    """
    if start is None or end is None:
        return None
    months = _month_number(end.date) - _month_number(start.date)
    return months if months >= 0 else None


def _month_number(day: str) -> int:
    return int(day[:4]) * 12 + int(day[5:7]) - 1


def _sort_evidence(study: Study, sort_by: SortField) -> tuple[Evidence, ...]:
    if sort_by == "enrollment":
        found = _number(study, "enrollment")
        return found[1] if found is not None else (Evidence(f"{_DESIGN}.enrollmentInfo.count", None),)
    attribute, path = _SORT_DATES[sort_by]
    value: StudyDate | None = getattr(study, attribute)
    return (Evidence(path, None if value is None else value.date),)
