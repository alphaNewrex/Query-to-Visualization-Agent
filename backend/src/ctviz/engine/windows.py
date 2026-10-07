"""Windows of periods on a date axis: the latest few, or those that end where the data ends.

The labels and their days come from the catalogue's period module, which the buckets of a date field use
too, so the window and its buckets always agree.
"""

from typing import Final

from ctviz.catalog.fields import Window
from ctviz.catalog.periods import period_of, periods_between, window_labels
from ctviz.contract.plan import TimeUnit

_PERIODS_PER_YEAR: Final = {"year": 1, "quarter": 4, "month": 12}


def length(window: Window) -> int:
    return len(window_labels(window))


def latest(window: Window, count: int) -> Window:
    """The last `count` periods of a window, or all of it when it is shorter."""
    labels = window_labels(window)
    return window if len(labels) <= count else Window(window.unit, labels[-count], window.last)


def ending_at(unit: TimeUnit, last: str, count: int) -> Window:
    """The `count` periods that end with the period `last`."""
    # Start far enough back to hold `count` periods, then keep the last ones.
    first_year = int(last[:4]) - count // _PERIODS_PER_YEAR[unit] - 1
    labels = periods_between(unit, label_of_day(unit, f"{first_year}-01-01"), last)
    return Window(unit, labels[-count], last)


def of_year(unit: TimeUnit, year: int) -> tuple[str, str]:
    """The first and the last period of a calendar year."""
    return label_of_day(unit, f"{year}-01-01"), label_of_day(unit, f"{year}-12-31")


def label_of_day(unit: TimeUnit, day: str) -> str:
    """The period holding a registry date; raises ValueError when the text is not one."""
    label = period_of(day, unit)
    if label is None:
        raise ValueError(f"Not a registry date: {day!r}.")
    return label
