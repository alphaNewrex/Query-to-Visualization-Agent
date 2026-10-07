"""Conversational messages: a `converse` plan is answered by a template and carries no follow-up scope."""

import pytest

from ctviz import examples
from ctviz.contract.plan import Converse
from ctviz.planning.conversation import describe
from ctviz.planning.planner import FakePlanner
from ctviz.planning.service import PlanService
from ctviz.planning.validate import check_plan
from ctviz.settings import Settings
from tests.unit.plan_samples import TODAY, Countries, aggregate, entity, plan, request

pytestmark = pytest.mark.anyio

TOPICS = ("greeting", "thanks", "capabilities", "small_talk")


def converse(topic: str) -> object:
    return plan(interpretation="Conversation.", entities=[], analysis={"kind": "converse", "topic": topic})


@pytest.mark.parametrize("topic", TOPICS)
def test_a_converse_plan_is_answered_without_data(topic: str) -> None:
    result = check_plan(converse(topic), request("Hi"), mode="model", countries=Countries(), today=TODAY)  # type: ignore[arg-type]

    assert result.outcome is not None and not result.blocking
    assert (result.outcome.kind, result.outcome.reason) == ("conversation", topic)
    assert isinstance(result.plan.analysis, Converse)


def test_capabilities_list_the_groupings_from_the_catalogue() -> None:
    result = check_plan(
        converse("capabilities"), request("Help"), mode="model", countries=Countries(), today=TODAY
    )  # type: ignore[arg-type]

    assert result.outcome is not None
    assert "phase" in result.outcome.message and "ClinicalTrials.gov" in result.outcome.message


def test_a_conversational_turn_is_never_a_follow_up() -> None:
    before = plan(entities=[entity("drug", "pembrolizumab")], analysis=aggregate("phase"))

    assert not describe(before, converse("greeting")).conversation.is_follow_up  # type: ignore[arg-type]
    assert not describe(converse("greeting"), before).conversation.is_follow_up  # type: ignore[arg-type]


async def test_a_conversational_plan_is_remembered_but_a_refusal_is_not() -> None:
    primary = FakePlanner([converse("greeting"), converse("greeting")])  # type: ignore[list-item]
    subject = PlanService(primary, None, Settings(), countries=Countries())
    asked = request("Hi")

    class Context:
        request_id = "t"

    await subject.produce(asked, TODAY, Context())
    again = await subject.produce(asked, TODAY, Context())

    assert again.is_cached and len(primary.calls) == 1 and again.outcome is not None


def test_suggestions_are_recorded_charts_asked_in_the_question_alone() -> None:
    chosen = examples.suggestions(examples.examples_directory(Settings()))

    assert 1 <= len(chosen) <= 3
    assert all(item.label == item.request.query for item in chosen)
