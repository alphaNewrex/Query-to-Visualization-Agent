"""Partial dates, period labels and ranges."""

import pytest

from ctviz.catalog.fields import Window
from ctviz.catalog.periods import (
    parse_date,
    period_of,
    period_range,
    periods_between,
    window_labels,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("2015-03-06", (2015, 3, 6)), ("2015-03", (2015, 3, 1)), ("2024-02-29", (2024, 2, 29))],
)
def test_a_month_reads_as_its_first_day(text: str, expected: tuple[int, int, int]) -> None:
    assert parse_date(text) == expected


@pytest.mark.parametrize("text", ["2015", "2015-13", "2023-02-29", "March 2015", "", "2015-3-6"])
def test_text_that_is_not_a_registry_date_is_no_date(text: str) -> None:
    assert parse_date(text) is None
    assert period_of(text, "year") is None


@pytest.mark.parametrize(
    ("date", "year", "quarter", "month"),
    [
        ("2022-05-17", "2022", "2022-Q2", "2022-05"),
        ("2022-04", "2022", "2022-Q2", "2022-04"),
        ("2022-12-31", "2022", "2022-Q4", "2022-12"),
        ("1985-01", "1985", "1985-Q1", "1985-01"),
    ],
)
def test_period_labels(date: str, year: str, quarter: str, month: str) -> None:
    assert (period_of(date, "year"), period_of(date, "quarter"), period_of(date, "month")) == (
        year,
        quarter,
        month,
    )


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("2015", ("2015-01-01", "2015-12-31")),
        ("2022-Q2", ("2022-04-01", "2022-06-30")),
        ("2022-06", ("2022-06-01", "2022-06-30")),
        ("2024-02", ("2024-02-01", "2024-02-29")),
        ("2023-02", ("2023-02-01", "2023-02-28")),
    ],
)
def test_bucket_ranges_start_on_the_first_day(label: str, expected: tuple[str, str]) -> None:
    assert period_range(label) == expected


def test_a_quarter_is_the_union_of_its_months() -> None:
    first, _ = period_range("2022-04")
    _, last = period_range("2022-06")
    assert period_range("2022-Q2") == (first, last)


def test_gaps_are_filled_by_listing_every_period() -> None:
    assert periods_between("year", "2015", "2018") == ["2015", "2016", "2017", "2018"]
    assert periods_between("quarter", "2023-Q3", "2024-Q2") == ["2023-Q3", "2023-Q4", "2024-Q1", "2024-Q2"]
    assert periods_between("month", "2023-11", "2024-02") == ["2023-11", "2023-12", "2024-01", "2024-02"]
    assert window_labels(Window("year", "2020", "2020")) == ["2020"]


def test_a_window_of_the_wrong_unit_is_refused() -> None:
    with pytest.raises(ValueError, match="quarter labels"):
        periods_between("quarter", "2015", "2016")
    with pytest.raises(ValueError, match="Not a period label"):
        period_range("2015-Q5")
