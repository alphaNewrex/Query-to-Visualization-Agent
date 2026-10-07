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
    assert row.evidence[1].path.endswith("locations[0].country")


def test_a_trial_missing_a_plotted_value_is_excluded_by_reason() -> None:
    plan = engine_plan(rows=PointRows("enrollment", "site_count", None))

    result = trial_rows(
        [study(1, enrollment=None), study(2, enrollment=5), study(3, enrollment=None)], plan, plan.scopes[0]
    )

    assert [row.study.nct_id for row in result.rows] == ["NCT00000002"]
    assert {reason: item.count for reason, item in result.excluded.items()} == {"no_enrollment": 2}
    assert result.seen == 3


def test_duration_is_whole_months_with_a_month_read_as_its_first_day_and_never_negative() -> None:
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
        [trial(1, "2020-03", "2021-01-15"), trial(2, "2021-05", "2020-01")], plan, plan.scopes[0]
    )

    assert [row.x for row in result.rows] == [10.0]
    assert result.excluded["no_duration_months"].count == 1


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
    assert rows.rows[0].evidence[0].path.endswith("enrollmentInfo.count")
