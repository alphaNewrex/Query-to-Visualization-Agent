"""A time axis that reaches the data date: periods still running or to come, and trials beyond the axis."""

import pytest

from ctviz.catalog.fields import Window

from .engine_memory import Run, answer, dim, plan, public, record, rows_of

pytestmark = pytest.mark.anyio  # the data date of the harness registry is 2026-10-06


def axis(key: str, unit: str) -> dict[str, object]:
    return {
        "kind": "aggregate",
        "dimension": key,
        "series": None,
        "time_unit": unit,
        "top_n": None,
        "statistic": None,
        "of": None,
    }


async def run(key: str, unit: str, window: Window, studies: list) -> Run:  # type: ignore[type-arg]
    p = plan(dim(key, unit=unit), pub=public(axis(key, unit)), window=window)
    return await answer(p, studies)


def codes(result: Run) -> list[str]:
    return [warning.code for warning in result.response.meta.warnings]


async def test_a_window_that_runs_past_the_data_date_says_which_periods_are_still_to_come() -> None:
    studies = [
        record(1, posted="2026-06-02"),
        record(2, posted="2026-07-02"),
        record(3, posted="2026-07-20"),
        record(4, posted="2026-09-02"),
        record(5, posted="2026-10-01"),
    ]

    result = await run("first_posted_date", "month", Window("month", "2026-06", "2026-12"), studies)

    assert codes(result) == ["partial_period", "future_periods"]
    future = next(w for w in result.response.meta.warnings if w.code == "future_periods")
    assert future.message.startswith("2026-11 to 2026-12 are after the data date (2026-10-06).")
    # The running month and the months to come are drawn, but none is the subject of the headline.
    message = result.response.message
    assert "2026-12" not in message.split("(")[0] and "2026-10" not in message.split("(")[0]
    assert message == (
        "1 trial first posted in 2026-09; the peak was 2 in 2026-07 "
        "(counted over the periods that have ended, 2026-06 to 2026-09)."
    )
    assert {row["first_posted_month"]: row["trial_count"] for row in rows_of(result.response)}["2026-12"] == 0


async def test_a_window_that_ends_in_the_running_period_only_says_so() -> None:
    studies = [record(1, posted="2025-03-02"), record(2, posted="2026-03-02")]

    result = await run("first_posted_date", "year", Window("year", "2025", "2026"), studies)

    assert codes(result) == ["partial_period"]


async def test_a_window_that_ends_before_the_data_date_has_no_period_warning() -> None:
    studies = [record(1, posted="2023-03-02"), record(2, posted="2024-03-02")]

    result = await run("first_posted_date", "year", Window("year", "2023", "2024"), studies)

    assert codes(result) == []
    assert "counted over" not in result.response.message


async def test_a_future_year_is_not_the_latest_year_of_the_headline() -> None:
    studies = [record(1, posted="2025-03-02"), record(2, posted="2025-04-02")]

    result = await run("first_posted_date", "year", Window("year", "2025", "2030"), studies)

    assert "future_periods" in codes(result)
    assert result.response.message.startswith(
        "2 trials first posted in 2025, the most of any period that has ended"
    )
    assert "0 trials" not in result.response.message


async def test_a_headline_about_the_biggest_bar_says_how_many_trials_lie_beyond_the_axis() -> None:
    """Pembrolizumab completing from 2024: the axis ends with the data's year, and 2027 holds more."""
    studies = [
        record(1, start="2020-01", completion="2024-05"),
        *(record(n, start="2020-01", completion="2025-05") for n in (2, 3)),
        *(record(n, start="2020-01", completion="2026-05") for n in (4, 5, 6)),
        *(record(n, start="2020-01", completion="2027-05") for n in (7, 8, 9, 10)),
    ]

    result = await run("completion_date", "year", Window("year", "2024", "2026"), studies)

    message = result.response.message
    assert "4 trials completed after 2026 are not shown" in message
    assert message.startswith(
        "2 trials completed in 2025, the most of any period that has ended (counted over"
    )
    assert "2026" not in message.split("(")[0]
    counts = result.response.meta.counts
    assert counts is not None and [e.count for e in counts.series[0].trials_excluded] == [4]
