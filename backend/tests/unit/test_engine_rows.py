"""Per-trial rows: numeric fields with their evidence, and the exclusions by reason."""

import pytest

from ctviz.ctgov.study import Study, parse_study
from ctviz.engine.execute import execute_plan
from ctviz.engine.lower import ListRows, PointRows
from ctviz.engine.rows import trial_rows
from ctviz.engine.strategy import ExecutionPlan, Limits, choose_strategy

from .engine_support import SPONSOR, FakeClient, FakeContext, bound, engine_plan, study

pytestmark = pytest.mark.anyio


def test_a_point_carries_its_numbers_colour_and_the_evidence_of_each() -> None:
    plan = engine_plan(rows=PointRows("enrollment", "site_count", bound(SPONSOR, "series")))

    result = trial_rows([study(1, enrollment=120, countries=["A", "B"])], plan, plan.scopes[0])

    (row,) = result.rows
    assert (row.x, row.y, row.color.key if row.color else None) == (120.0, 2.0, "Acme")
    assert row.evidence[0].path.endswith("enrollmentInfo.count")
    assert row.evidence[0].excerpt == "120"
    # Two sites: the last one lies at index 1, and nothing follows it. That proves the count of 2.
    assert [
        (item.path.rsplit("contactsLocationsModule.", 1)[-1], item.excerpt) for item in row.evidence[1:3]
    ] == [
        ("locations[1].country", "B"),
        ("locations[2]", None),
    ]


def test_a_trial_missing_a_plotted_value_is_excluded_by_reason() -> None:
    plan = engine_plan(rows=PointRows("enrollment", "site_count", None))

    result = trial_rows(
        [
            study(1, enrollment=None, countries=["A"]),
            study(2, enrollment=5, countries=["A"]),
            study(3, enrollment=None, countries=["A"]),
            study(4, enrollment=7),  # lists no site, so it has no site count
        ],
        plan,
        plan.scopes[0],
    )

    assert [row.study.nct_id for row in result.rows] == ["NCT00000002"]
    assert {reason: item.count for reason, item in result.excluded.items()} == {
        "no_enrollment": 2,
        "no_site_count": 1,
    }
    assert result.seen == 4


def test_duration_is_calendar_months_and_the_days_of_the_dates_count_as_parts_of_a_month() -> None:
    plan = engine_plan(rows=PointRows("duration_months", "enrollment", None))

    def trial(number: int, start: str, end: str) -> Study:
        record = {
            "protocolSection": {
                "identificationModule": {"nctId": f"NCT{number:08d}"},
                "statusModule": {
                    "startDateStruct": {"date": start},
                    "completionDateStruct": {"date": end},
                },
                "designModule": {"enrollmentInfo": {"count": 10}},
            }
        }
        return parse_study(record)

    result = trial_rows(
        [
            trial(1, "2020-03", "2021-01-15"),  # 10 months and 14 days
            trial(2, "2021-05", "2020-01"),  # ends first
            trial(3, "2020-03", "2021-03"),  # months only: whole
            trial(4, "2020-01-31", "2020-02-01"),  # a day, not a month
            trial(5, "2020-01-15", "2021-01-14"),  # a day short of a year
            trial(6, "2020-02-15", "2020-02-10"),  # ends five days before it starts
        ],
        plan,
        plan.scopes[0],
    )

    assert [row.x for row in result.rows] == [10.452, 12.0, 0.034, 11.968]
    assert result.excluded["no_duration_months"].count == 2


async def test_a_trial_list_is_one_sorted_page_with_the_sort_field_as_evidence() -> None:
    plan = engine_plan(rows=ListRows("enrollment", "desc"), top_n=2)
    trials = [study(n, enrollment=n * 10) for n in range(1, 6)]
    client = FakeClient(trials)
    xp = choose_strategy(plan, {"s0": 5}, Limits(1000, 5000, 60), prefer_walk=False)
    assert isinstance(xp, ExecutionPlan)

    result = await execute_plan(plan, xp, client, FakeContext())

    (rows,) = result.rows
    assert (rows.strategy, rows.matched, len(rows.rows)) == ("sorted_page", 5, 2)
    assert rows.excluded["beyond_listed_rows"].count == 3
    # Every cell of the row is quoted, the sort column among them.
    assert any(item.path.endswith("enrollmentInfo.count") for item in rows.rows[0].evidence)
    assert rows.rows[0].evidence[0].path.endswith("identificationModule.nctId")
