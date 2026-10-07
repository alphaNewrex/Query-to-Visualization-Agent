"""Partial dates, period labels and bucket ranges for year, quarter and month.

Registry dates are `YYYY-MM-DD` or `YYYY-MM`, and the server reads a month as its first day, so local
code does the same and the two paths agree. Periods are strings, `2024`, `2024-Q2`, `2024-06`, and are
never parsed as local-time dates, which would shift a year label by one in some time zones.
"""

import calendar
import re
from collections.abc import Iterator
from typing import Final

from ctviz.catalog.fields import Window
from ctviz.contract.plan import TimeUnit

_DATE: Final = re.compile(r"(\d{4})-(\d{2})(?:-(\d{2}))?")
_PERIOD: Final = re.compile(r"(\d{4})(?:-Q([1-4])|-(\d{2}))?")


def parse_date(text: str) -> tuple[int, int, int] | None:
    """`(year, month, day)` of a registry date, the day being 1 for a month; None when it is not one."""
    found = _DATE.fullmatch(text)
    if found is None:
        return None
    year, month, day = int(found[1]), int(found[2]), int(found[3] or 1)
    if not 1 <= month <= 12 or not 1 <= day <= calendar.monthrange(year, month)[1]:
        return None
    return year, month, day


def period_of(date_text: str, unit: TimeUnit) -> str | None:
    """The label of the period holding a registry date, None when the text is not a date."""
    parsed = parse_date(date_text)
    if parsed is None:
        return None
    return _label(parsed[0], parsed[1], unit)


def period_range(label: str) -> tuple[str, str]:
    """The first and last day of a period, both inclusive, as `RANGE[first,last]` wants them."""
    year, first_month, last_month = _bounds(label)
    last = calendar.monthrange(year, last_month)[1]
    return f"{year:04d}-{first_month:02d}-01", f"{year:04d}-{last_month:02d}-{last:02d}"


def periods_between(unit: TimeUnit, first: str, last: str) -> list[str]:
    """Every period label from `first` to `last`, both inclusive: the zero-filled axis of a time series."""
    return list(_walk(unit, first, last))


def window_labels(window: Window) -> list[str]:
    return periods_between(window.unit, window.first, window.last)


def _label(year: int, month: int, unit: TimeUnit) -> str:
    if unit == "year":
        return f"{year:04d}"
    if unit == "quarter":
        return f"{year:04d}-Q{(month - 1) // 3 + 1}"
    return f"{year:04d}-{month:02d}"


def _bounds(label: str) -> tuple[int, int, int]:
    """The year of a period label and the first and last month it covers."""
    found = _PERIOD.fullmatch(label)
    if found is None:
        raise ValueError(f"Not a period label: {label!r}.")
    year = int(found[1])
    if found[2]:
        quarter = int(found[2])
        return year, quarter * 3 - 2, quarter * 3
    if found[3]:
        month = int(found[3])
        if not 1 <= month <= 12:
            raise ValueError(f"Not a period label: {label!r}.")
        return year, month, month
    return year, 1, 12


def _walk(unit: TimeUnit, first: str, last: str) -> Iterator[str]:
    step = {"year": 12, "quarter": 3, "month": 1}[unit]
    year, month, _ = _bounds(first)
    end_year, end_month, _ = _bounds(last)
    index, end = year * 12 + month - 1, end_year * 12 + end_month - 1
    # A label of the wrong unit would silently give a different axis, so the units must agree.
    if _label(year, month, unit) != first or _label(end_year, end_month, unit) != last:
        raise ValueError(f"{first!r} and {last!r} are not {unit} labels.")
    while index <= end:
        yield _label(index // 12, index % 12 + 1, unit)
        index += step
