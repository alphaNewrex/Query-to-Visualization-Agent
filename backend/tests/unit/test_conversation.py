"""Follow-ups: grounding with a previous plan, the diff of `meta.conversation`, the prompt, the cache key."""

from typing import Any

import pytest

from ctviz.contract.plan import Clarify, QueryPlan
from ctviz.contract.request import QueryRequest
from ctviz.planning.conversation import describe
from ctviz.planning.planner import FakePlanner
from ctviz.planning.prompt import build_instructions, user_message
from ctviz.planning.service import PlanService
from ctviz.planning.validate import PlanCheck, check_plan
from ctviz.settings import Settings
from tests.unit.plan_samples import TODAY, Countries, aggregate, entity, plan, request

pytestmark = pytest.mark.anyio

PEMBRO = entity("drug", "pembrolizumab")
SINCE_2015 = {"year_from": 2015}


def follow_up(message: str, before: QueryPlan, **fields: Any) -> QueryRequest:
    turn = {"query": "How many pembrolizumab trials per year since 2015?", "plan": before.model_dump()}
    return request(message, previous=turn, **fields)


def check(candidate: QueryPlan, asked: QueryRequest) -> PlanCheck:
    return check_plan(candidate, asked, mode="model", countries=Countries(), today=TODAY)


def blocked(result: PlanCheck) -> set[str]:
    return {issue.code for issue in result.blocking}


BEFORE = plan(
    entities=[PEMBRO],
    analysis=aggregate("start_date", time_unit="year"),
    filters={**SINCE_2015, "phases": ["PHASE3"], "evidence": [{"family": "phases", "phrase": "phase 3"}]},
)

# --- grounding ---------------------------------------------------------------------------------------


def test_a_value_carried_over_unchanged_is_grounded_without_being_in_the_message() -> None:
    again = plan(
        entities=[PEMBRO],
        analysis=aggregate("start_date", series="overall_status", time_unit="year"),
        filters=BEFORE.filters.model_dump(),
    )
    result = check(again, follow_up("now split that by status", BEFORE))
    assert result.blocking == () and result.adjustments == ()
    assert result.plan.filters.year_from == 2015 and result.plan.filters.phases == ["PHASE3"]


def test_the_same_plan_without_a_previous_turn_is_ungrounded() -> None:
    asked = request("now split that by status")
    result = check(plan(entities=[PEMBRO], filters=SINCE_2015), asked)
    assert blocked(result) == {"ungrounded_entity", "ungrounded_year"}


def test_a_value_in_neither_the_message_nor_the_previous_plan_is_rejected() -> None:
    other = plan(
        entities=[entity("drug", "nivolumab")],
        filters={"year_from": 2019, "statuses": ["RECRUITING"], "evidence": []},
    )
    result = check(other, follow_up("same but for the competing drug", BEFORE))
    assert blocked(result) == {"ungrounded_entity", "ungrounded_year", "ungrounded_filter"}


def test_a_changed_value_is_grounded_only_by_the_message() -> None:
    changed = plan(entities=[PEMBRO], filters={"year_from": 2020})
    assert blocked(check(changed, follow_up("only since 2020", BEFORE))) == set()
    assert blocked(check(changed, follow_up("only more recent ones", BEFORE))) == {"ungrounded_year"}


def test_a_carried_filter_must_be_the_previous_one_exactly() -> None:
    widened = plan(entities=[PEMBRO], filters={"phases": ["PHASE2", "PHASE3"], "evidence": []})
    result = check(widened, follow_up("also include the other late ones", BEFORE))
    assert "ungrounded_filter" in blocked(result)


def test_a_carried_number_is_grounded() -> None:
    before = plan(entities=[PEMBRO], analysis=aggregate("sponsor", top_n=5))
    again = plan(entities=[PEMBRO], analysis=aggregate("country", top_n=5))
    result = check(again, follow_up("by country instead", before))
    assert result.blocking == () and result.adjustments == ()
    fresh = plan(entities=[PEMBRO], analysis=aggregate("country", top_n=7))
    assert [a.code for a in check(fresh, follow_up("by country instead", before)).adjustments] == [
        "ungrounded_number"
    ]


def test_the_interpretation_may_repeat_a_carried_year() -> None:
    again = plan(entities=[PEMBRO], interpretation="Trials since 2015, by phase.", filters=SINCE_2015)
    assert check(again, follow_up("by phase", BEFORE)).adjustments == ()
    alone = check(again, request("by phase"))
    assert [a.code for a in alone.adjustments] == ["text_hygiene"]


def test_a_clarification_is_completed_by_the_next_message() -> None:
    asked = plan(
        entities=[], analysis={"kind": "clarify", "reason": "missing_entity", "missing": ["drug_name"]}
    )
    assert isinstance(asked.analysis, Clarify)
    answered = plan(entities=[PEMBRO], analysis=aggregate("start_date", time_unit="year"))
    result = check(answered, follow_up("pembrolizumab", asked))
    assert result.blocking == () and result.outcome is None
    assert describe(asked, answered).conversation.is_follow_up


# --- the diff ----------------------------------------------------------------------------------------


def test_a_split_by_phase_carries_the_scope_and_adds_the_split() -> None:
    after = plan(
        entities=[PEMBRO],
        analysis=aggregate("start_date", series="phase", time_unit="year"),
        filters=SINCE_2015,
    )
    before = plan(entities=[PEMBRO], analysis=aggregate("start_date", time_unit="year"), filters=SINCE_2015)
    described = describe(before, after)
    assert described.conversation.is_follow_up
    assert described.conversation.carried_over == [
        "drug: pembrolizumab",
        "from 2015",
        "grouped by start date",
    ]
    assert described.conversation.changed == ["split by phase"]
    assert described.scope == ("drug: pembrolizumab", "from 2015")


def test_another_drug_replaces_the_first_and_keeps_the_rest() -> None:
    before = plan(entities=[PEMBRO], filters=SINCE_2015)
    after = plan(entities=[entity("drug", "nivolumab")], filters=SINCE_2015)
    described = describe(before, after).conversation
    assert described.changed == ["drug: nivolumab replaces pembrolizumab"]
    assert "from 2015" in described.carried_over


def test_filters_years_numbers_and_charts_are_described() -> None:
    before = plan(entities=[PEMBRO], filters={"year_from": 2015, "statuses": ["WITHDRAWN"]})
    after = plan(
        entities=[PEMBRO, entity("country", "Germany", "exclude")],
        analysis=aggregate("sponsor", top_n=5),
        filters={"year_from": 2020, "exclude_statuses": ["WITHDRAWN"]},
        chart_preference="bar_chart",
    )
    changed = describe(before, after).conversation.changed
    assert "added excluded country: Germany" in changed
    assert "removed status filter (withdrawn)" in changed
    assert "added excluded status: withdrawn" in changed
    assert "from 2020 (was 2015)" in changed
    assert "top 5" in changed and "chart: bar chart" in changed


def test_nothing_in_common_is_a_new_question_with_empty_lists() -> None:
    before = plan(entities=[PEMBRO], analysis=aggregate("start_date", time_unit="year"))
    after = plan(
        entities=[entity("condition", "asthma")], analysis={"kind": "total", "statistic": None, "of": None}
    )
    described = describe(before, after)
    assert not described.conversation.is_follow_up
    assert described.conversation.carried_over == [] and described.conversation.changed == []


def test_without_a_previous_plan_the_conversation_is_empty() -> None:
    described = describe(None, plan())
    assert described.conversation.model_dump() == {"is_follow_up": False, "carried_over": [], "changed": []}
    assert described.scope == ()


# --- the prompt and the cache --------------------------------------------------------------------------


def test_the_message_shows_the_previous_turn_only_when_there_is_one() -> None:
    plain = user_message(request("How many trials?"), TODAY)
    assert "Previous" not in plain and plain.endswith("Question: How many trials?")
    shown = user_message(follow_up("by phase", BEFORE), TODAY)
    assert "Previous question: How many pembrolizumab trials per year since 2015?" in shown
    assert f"Previous plan: {BEFORE.model_dump_json()}" in shown
    assert shown.endswith("Question: by phase")


def test_the_instructions_have_a_follow_up_rule_and_no_phrase_list() -> None:
    assert "17. Follow-ups" in build_instructions()


async def test_the_plan_cache_key_includes_the_previous_turn() -> None:
    primary = FakePlanner([plan(), plan(), plan()])
    subject = PlanService(primary, None, Settings(), countries=Countries())

    class Context:
        request_id = "test"

    alone = request("How many pembrolizumab trials?")
    with_previous = follow_up("How many pembrolizumab trials?", BEFORE)
    other_previous = follow_up("How many pembrolizumab trials?", plan(entities=[PEMBRO], filters=SINCE_2015))
    for asked in (alone, with_previous, other_previous, with_previous):
        await subject.produce(asked, TODAY, Context())
    assert len(primary.calls) == 3  # the repeated follow-up was served from the cache
