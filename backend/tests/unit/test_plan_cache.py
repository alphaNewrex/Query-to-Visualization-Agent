"""The plan cache of `PlanService` (section 4.9): what is remembered, for how long, and what is not.

Every planner here is a `FakePlanner` scripted for exactly the calls a test expects, so a model call that
the cache should have saved ends the test with the fake's "called more often than scripted" error.
"""

from collections.abc import Sequence
from datetime import timedelta
from typing import Any

import anyio
import pytest

from ctviz.contract.request import QueryRequest
from ctviz.errors import PlannerUnavailableError
from ctviz.planning.planner import FakePlanner, Message, PlannerRefused, PlannerResult, PlannerUnavailable
from ctviz.planning.service import PLAN_TTL_S, PlannedQuery, PlanService
from ctviz.settings import Settings
from tests.unit.plan_samples import TODAY, Countries, entity, plan, request

pytestmark = pytest.mark.anyio


class Context:
    request_id = "test-request"


class Clock:
    """A monotonic clock that moves only when a test says so."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class SlowPlanner(FakePlanner):
    """A model that takes a moment to answer, so that identical requests overlap."""

    async def draft(
        self, *, instructions: str, messages: Sequence[Message], max_output_tokens: int
    ) -> PlannerResult:
        await anyio.sleep(0.05)
        return await super().draft(
            instructions=instructions, messages=messages, max_output_tokens=max_output_tokens
        )


GOOD = plan()
UNGROUNDED = plan(entities=[entity("drug", "Keytruda")])  # the question below says pembrolizumab
QUESTION = request("How are pembrolizumab trials distributed across phases?")


def service(primary: FakePlanner | None, *, clock: Clock | None = None, **settings: Any) -> PlanService:
    return PlanService(primary, None, Settings(**settings), countries=Countries(), clock=clock or Clock())


async def produce(subject: PlanService, asked: QueryRequest = QUESTION, *, on: Any = TODAY) -> PlannedQuery:
    return await subject.produce(asked, on, Context())


# --- what is remembered ------------------------------------------------------------------------------


async def test_a_repeated_question_costs_no_model_call_and_gets_the_same_plan() -> None:
    primary = FakePlanner([GOOD])
    subject = service(primary)

    first = await produce(subject)
    second = await produce(subject)

    assert len(primary.calls) == 1
    assert (first.is_cached, second.is_cached) == (False, True)
    assert (second.plan, second.info) == (first.plan, first.info)
    assert (second.adjustments, second.warnings, second.outcome) == (
        first.adjustments,
        first.warnings,
        first.outcome,
    )


async def test_a_hit_belongs_to_the_request_that_asked_for_it() -> None:
    primary = FakePlanner([GOOD])
    subject = service(primary)
    await produce(subject)
    traceless = request(QUESTION.query, options={"include_trace": False, "citations_per_datum": 2})

    planned = await produce(subject, traceless)

    assert planned.is_cached and len(primary.calls) == 1
    assert planned.request == traceless and planned.options == traceless.options


async def test_whitespace_does_not_make_a_new_question() -> None:
    primary = FakePlanner([GOOD])
    subject = service(primary)
    await produce(subject)

    padded = request("  How are pembrolizumab\ttrials   distributed\nacross phases?  ")

    assert (await produce(subject, padded)).is_cached


async def test_a_plan_the_repair_turn_fixed_is_remembered_with_its_record() -> None:
    primary = FakePlanner([UNGROUNDED, GOOD])
    subject = service(primary)
    await produce(subject)

    again = await produce(subject)

    assert again.is_cached and again.info.is_repaired and again.info.attempts == 2
    assert [a.action for a in again.adjustments] == ["repaired"] and len(primary.calls) == 2


async def test_a_clarification_is_asked_again() -> None:
    asking = plan(
        entities=[], analysis={"kind": "clarify", "reason": "missing_entity", "missing": ["drug_name"]}
    )
    primary = FakePlanner([asking, asking])
    subject = service(primary)
    question = request("How has the number of trials for this drug changed?")

    first = await produce(subject, question)
    again = await produce(subject, question)

    assert first.outcome is not None and not again.is_cached and again.outcome == first.outcome
    assert len(primary.calls) == 2


async def test_an_unsupported_plan_the_model_wrote_is_asked_again() -> None:
    declined = plan(
        entities=[],
        analysis={"kind": "unsupported", "category": "other", "reason": "Not about registered trials."},
    )
    primary = FakePlanner([declined, declined])
    subject = service(primary)
    question = request("What is the capital of France?")

    first = await produce(subject, question)
    again = await produce(subject, question)

    assert first.outcome is not None and first.outcome.kind == "unsupported"
    assert not again.is_cached and again.outcome == first.outcome and len(primary.calls) == 2


# --- what makes a new plan ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "other",
    [
        request("Which phases do pembrolizumab trials fall into?"),
        request(QUESTION.query, condition="melanoma"),
        request(QUESTION.query, start_year=2020),
        request(QUESTION.query, drug_name="Keytruda"),
    ],
    ids=["question", "condition", "start_year", "drug_name"],
)
async def test_another_question_or_another_field_is_planned_again(other: QueryRequest) -> None:
    primary = FakePlanner([GOOD, GOOD])
    subject = service(primary)
    await produce(subject)

    planned = await produce(subject, other)

    assert not planned.is_cached and len(primary.calls) == 2


async def test_a_plan_is_not_reused_on_another_day() -> None:
    primary = FakePlanner([GOOD, GOOD])
    subject = service(primary)
    await produce(subject)

    tomorrow = await produce(subject, on=TODAY + timedelta(days=1))

    assert not tomorrow.is_cached and len(primary.calls) == 2


@pytest.mark.parametrize("changed", [{"planner_model": "gpt-5.4"}, {"planner_effort": "high"}])
def test_the_key_names_the_model_and_the_effort(changed: dict[str, str]) -> None:
    assert service(None)._key(QUESTION, TODAY) != service(None, **changed)._key(QUESTION, TODAY)


async def test_a_plan_is_forgotten_after_twenty_four_hours() -> None:
    clock = Clock()
    primary = FakePlanner([GOOD, GOOD])
    subject = service(primary, clock=clock)
    await produce(subject)

    clock.now = PLAN_TTL_S - 1
    assert (await produce(subject)).is_cached
    clock.now = PLAN_TTL_S + 1
    assert not (await produce(subject)).is_cached

    assert PLAN_TTL_S == 24 * 60 * 60 and len(primary.calls) == 2


async def test_the_cache_holds_the_number_of_plans_the_settings_allow() -> None:
    primary = FakePlanner([GOOD, GOOD, GOOD])
    subject = service(primary, plan_cache_size=1)
    another = request("Which phases do pembrolizumab trials fall into?")

    await produce(subject)
    await produce(subject, another)  # takes the only place
    assert (await produce(subject, another)).is_cached
    assert not (await produce(subject)).is_cached

    assert len(primary.calls) == 3


# --- what is not remembered --------------------------------------------------------------------------


async def test_a_failed_attempt_is_not_remembered() -> None:
    primary = FakePlanner([PlannerUnavailable("down"), GOOD])
    subject = service(primary)

    with pytest.raises(PlannerUnavailableError):
        await produce(subject)
    planned = await produce(subject)

    assert planned.outcome is None and not planned.is_cached and len(primary.calls) == 2


async def test_a_plan_settled_after_a_spent_repair_turn_is_not_remembered() -> None:
    primary = FakePlanner([UNGROUNDED, UNGROUNDED, GOOD])
    subject = service(primary)

    first = await produce(subject)
    assert first.outcome is not None and first.outcome.reason == "could_not_interpret"
    second = await produce(subject)

    assert second.outcome is None and not second.is_cached and len(primary.calls) == 3
    assert (await produce(subject)).is_cached  # the good plan is kept, the failure was not


async def test_a_filter_dropped_after_the_repair_turn_is_not_remembered() -> None:
    unsupported_filter = plan(
        filters={"statuses": ["RECRUITING"], "evidence": [{"family": "statuses", "phrase": "halted"}]}
    )
    primary = FakePlanner([unsupported_filter, unsupported_filter, GOOD])
    subject = service(primary)

    first = await produce(subject)
    assert [w.code for w in first.warnings] == ["filter_dropped"]
    second = await produce(subject)

    assert second.warnings == () and not second.is_cached and len(primary.calls) == 3


async def test_a_refusal_is_not_remembered() -> None:
    primary = FakePlanner([PlannerRefused("no"), GOOD])
    subject = service(primary)

    first = await produce(subject)
    assert first.outcome is not None and first.outcome.kind == "unsupported"
    second = await produce(subject)

    assert second.outcome is None and not second.is_cached and len(primary.calls) == 2


# --- use_cache: false ---------------------------------------------------------------------------------


async def test_use_cache_false_neither_reads_nor_writes_the_cache() -> None:
    primary = FakePlanner([GOOD, GOOD, GOOD])
    subject = service(primary)
    bypassing = request(QUESTION.query, options={"use_cache": False})

    first = await produce(subject, bypassing)
    stored = await produce(subject)  # the bypassing request left nothing behind
    assert (first.is_cached, stored.is_cached, len(primary.calls)) == (False, False, 2)
    assert (await produce(subject)).is_cached  # an ordinary request is served from the cache
    again = await produce(subject, bypassing)  # a bypassing one is not, though the plan is there

    assert not again.is_cached and len(primary.calls) == 3


# --- identical requests in flight ----------------------------------------------------------------------


async def test_identical_requests_in_flight_share_one_model_call() -> None:
    primary = SlowPlanner([GOOD])
    subject = service(primary)
    plans: list[PlannedQuery] = []

    async def ask() -> None:
        plans.append(await produce(subject))

    async with anyio.create_task_group() as group:
        for _ in range(3):
            group.start_soon(ask)

    assert len(primary.calls) == 1
    assert sorted(planned.is_cached for planned in plans) == [False, True, True]
    assert plans[0].plan == plans[1].plan == plans[2].plan


async def test_a_failure_is_shared_by_the_requests_waiting_for_it_and_then_forgotten() -> None:
    primary = SlowPlanner([PlannerUnavailable("down"), GOOD])
    subject = service(primary)
    failures: list[PlannerUnavailableError] = []

    async def ask() -> None:
        try:
            await produce(subject)
        except PlannerUnavailableError as error:
            failures.append(error)

    async with anyio.create_task_group() as group:
        for _ in range(2):
            group.start_soon(ask)

    assert len(failures) == 2 and len(primary.calls) == 1
    assert (await produce(subject)).outcome is None
