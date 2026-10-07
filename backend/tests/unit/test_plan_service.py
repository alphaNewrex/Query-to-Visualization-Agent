"""`PlanService` over `FakePlanner`: failure policy, repair turn, structured mode, supplied plans."""

import pytest

from ctviz.contract.plan import QueryPlan
from ctviz.contract.request import AnalysisRequest, QueryRequest
from ctviz.errors import InvalidRequest, PlannerUnavailableError
from ctviz.planning.planner import (
    FakePlanner,
    PlannerMisconfigured,
    PlannerOutputError,
    PlannerRefused,
    PlannerUnavailable,
)
from ctviz.planning.service import FIRST_TOKEN_CAP, RETRY_TOKEN_CAP, PlannedQuery, PlanService
from ctviz.settings import Settings
from tests.unit.plan_samples import TODAY, Countries, aggregate, entity, plan, request

pytestmark = pytest.mark.anyio


class Context:
    request_id = "test-request"


def service(primary: FakePlanner | None, fallback: FakePlanner | None = None) -> PlanService:
    return PlanService(primary, fallback, Settings(), countries=Countries())


GOOD = plan()
UNGROUNDED = plan(entities=[entity("drug", "Keytruda")])  # the question below says pembrolizumab
QUESTION = request("How are pembrolizumab trials distributed across phases?")


async def produce(subject: PlanService, asked: QueryRequest = QUESTION) -> PlannedQuery:
    return await subject.produce(asked, TODAY, Context())


def errors_of(error: InvalidRequest) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = error.details["errors"]  # type: ignore[assignment]
    return errors


async def test_a_good_plan_takes_one_call_and_is_canonical() -> None:
    primary = FakePlanner([GOOD])
    planned = await produce(service(primary))
    assert planned.info.attempts == 1 and not planned.info.is_repaired and not planned.info.is_fallback
    assert planned.info.mode == "llm" and planned.info.prompt_version == "plan-v1"
    assert planned.info.model == "fake-model" and planned.info.usage is not None
    assert planned.info.usage.input_tokens == 10 and planned.outcome is None
    assert planned.plan.filters.date_field == "start_date"
    assert planned.request == QUESTION and planned.options == QUESTION.options


async def test_the_first_call_gets_the_date_the_fields_and_the_question() -> None:
    primary = FakePlanner([GOOD])
    await produce(service(primary), request("Phases of this drug", drug_name="Pembrolizumab"))
    instructions, messages, cap = primary.calls[0]
    assert instructions.startswith("You translate") and cap == FIRST_TOKEN_CAP
    assert [m["role"] for m in messages] == ["user"]
    assert 'Structured fields: {"drug_name": ["Pembrolizumab"]}' in messages[0]["content"]


async def test_blocking_issues_get_one_stateless_repair_turn() -> None:
    primary = FakePlanner([UNGROUNDED, GOOD])
    planned = await produce(service(primary))
    assert planned.info.attempts == 2 and planned.info.is_repaired
    assert [a.action for a in planned.adjustments] == ["repaired"]
    assert planned.adjustments[0].code == "ungrounded_entity"
    _, messages, _ = primary.calls[1]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert "Keytruda" in messages[1]["content"] and "ungrounded_entity" in messages[2]["content"]


async def test_a_plan_that_is_still_blocked_after_the_repair_becomes_could_not_interpret() -> None:
    planned = await produce(service(FakePlanner([UNGROUNDED, UNGROUNDED])))
    assert planned.outcome is not None and planned.outcome.reason == "could_not_interpret"
    assert [w.code for w in planned.warnings] == ["plan_not_repaired"]
    assert planned.info.attempts == 2


async def test_an_ungrounded_filter_that_survives_the_repair_is_dropped() -> None:
    bad = plan(filters={"statuses": ["RECRUITING"], "evidence": [{"family": "statuses", "phrase": "halted"}]})
    planned = await produce(service(FakePlanner([bad, bad])))
    assert planned.plan.filters.statuses == [] and planned.outcome is None
    assert [w.code for w in planned.warnings] == ["filter_dropped"]


async def test_a_cut_off_output_is_retried_with_a_doubled_cap() -> None:
    primary = FakePlanner([PlannerOutputError("cut"), GOOD])
    planned = await produce(service(primary))
    assert [call[2] for call in primary.calls] == [FIRST_TOKEN_CAP, RETRY_TOKEN_CAP]
    assert planned.info.attempts == 2 and not planned.info.is_fallback


async def test_a_second_cut_off_goes_to_the_fallback_model() -> None:
    primary = FakePlanner([PlannerOutputError("cut"), PlannerOutputError("cut")])
    fallback = FakePlanner([GOOD], model="fallback-model")
    planned = await produce(service(primary, fallback))
    assert planned.info.is_fallback and planned.info.model == "fallback-model" and planned.info.attempts == 3


async def test_an_unavailable_model_goes_straight_to_the_fallback() -> None:
    primary = FakePlanner([PlannerUnavailable("down")])
    fallback = FakePlanner([GOOD], model="fallback-model")
    planned = await produce(service(primary, fallback))
    assert planned.info.is_fallback and planned.info.attempts == 2 and len(primary.calls) == 1


async def test_the_repair_goes_to_the_model_that_wrote_the_plan() -> None:
    primary = FakePlanner([PlannerUnavailable("down")])
    fallback = FakePlanner([UNGROUNDED, GOOD])
    planned = await produce(service(primary, fallback))
    assert len(fallback.calls) == 2 and planned.info.is_repaired and planned.info.attempts == 3


async def test_no_plan_after_every_attempt_is_a_retryable_503() -> None:
    primary = FakePlanner([PlannerUnavailable("down")])
    fallback = FakePlanner([PlannerUnavailable("down")])
    with pytest.raises(PlannerUnavailableError) as raised:
        await produce(service(primary, fallback))
    assert raised.value.details == {"reason": "transient"} and raised.value.is_retryable


async def test_the_budget_is_three_model_calls() -> None:
    primary = FakePlanner([PlannerOutputError("cut"), PlannerOutputError("cut")])
    fallback = FakePlanner([UNGROUNDED, GOOD])
    planned = await produce(service(primary, fallback))
    # Three calls are spent before the repair turn could run, so the plan is settled without it.
    assert planned.info.attempts == 3 and not planned.info.is_repaired
    assert planned.outcome is not None and planned.outcome.reason == "could_not_interpret"
    assert len(fallback.calls) == 1


async def test_a_misconfigured_model_is_not_hidden_behind_the_fallback() -> None:
    primary = FakePlanner([PlannerMisconfigured("bad key")])
    fallback = FakePlanner([GOOD])
    with pytest.raises(PlannerUnavailableError) as raised:
        await produce(service(primary, fallback))
    assert raised.value.details == {"reason": "configuration"} and not raised.value.is_retryable
    assert fallback.calls == []


async def test_a_refusal_is_an_unsupported_outcome_with_a_fixed_message() -> None:
    planned = await produce(service(FakePlanner([PlannerRefused("no")])))
    assert planned.outcome is not None and planned.outcome.kind == "unsupported"
    assert planned.plan.analysis.kind == "unsupported"
    assert planned.info.attempts == 1


async def test_no_model_configured_is_a_503_that_names_the_alternatives() -> None:
    with pytest.raises(PlannerUnavailableError) as raised:
        await produce(service(None))
    assert raised.value.details == {"reason": "not_configured"} and "structured" in raised.value.message


async def test_a_clarification_the_model_wrote_is_an_outcome_without_repair() -> None:
    asking = plan(
        entities=[], analysis={"kind": "clarify", "reason": "missing_entity", "missing": ["drug_name"]}
    )
    primary = FakePlanner([asking])
    planned = await produce(service(primary), request("How has the number of trials for this drug changed?"))
    assert planned.outcome is not None and planned.outcome.reason == "missing_entity"
    assert len(primary.calls) == 1


async def test_a_country_field_that_is_not_a_country_is_a_422_before_any_model_call() -> None:
    primary = FakePlanner([GOOD])
    with pytest.raises(InvalidRequest) as raised:
        await produce(service(primary), request("Phases", country="Fraance"))
    assert errors_of(raised.value)[0]["path"] == "/country/0"
    assert "France" in errors_of(raised.value)[0]["message"] and primary.calls == []


async def test_structured_mode_never_calls_a_model_and_says_the_question_was_not_read() -> None:
    primary = FakePlanner([])
    asked = request(
        "Compare phases",
        compare={"field": "drug_name", "values": ["pembrolizumab", "nivolumab"]},
        group_by=["phase"],
        options={"planner": "structured"},
    )
    planned = await produce(service(primary), asked)
    assert primary.calls == [] and planned.info.mode == "structured" and planned.info.attempts == 0
    assert planned.warnings[0].code == "question_not_interpreted"
    assert [(e.value, e.role) for e in planned.plan.entities] == [
        ("pembrolizumab", "compare"),
        ("nivolumab", "compare"),
    ]


async def test_structured_mode_keeps_the_comparison_when_a_field_names_one_side_of_it() -> None:
    asked = request(
        "Compare phases",
        drug_name="pembrolizumab",
        compare={"field": "drug_name", "values": ["Pembrolizumab", "nivolumab"]},
        group_by=["phase"],
        options={"planner": "structured"},
    )
    planned = await produce(service(None), asked)
    assert [(e.value, e.role) for e in planned.plan.entities] == [
        ("Pembrolizumab", "compare"),
        ("nivolumab", "compare"),
    ]
    assert planned.adjustments == ()


async def test_structured_mode_works_with_no_model_at_all() -> None:
    asked = request(
        "Phases", drug_name="pembrolizumab", group_by=["phase"], options={"planner": "structured"}
    )
    planned = await produce(service(None), asked)
    assert planned.plan.analysis == aggregate_plan(top_n=15).analysis


def aggregate_plan(**fields: object) -> QueryPlan:
    return plan(analysis=aggregate("phase", **fields))


async def test_structured_mode_rejects_an_unknown_country_in_a_field_before_planning() -> None:
    asked = request("Phases", country="Fraance", group_by=["phase"], options={"planner": "structured"})
    with pytest.raises(InvalidRequest):
        await produce(service(None), asked)


def test_a_supplied_plan_is_accepted_without_a_model() -> None:
    planned = service(None).accept(AnalysisRequest(plan=GOOD), TODAY)
    assert planned.info.mode == "supplied_plan" and planned.request is None and planned.info.attempts == 0
    assert planned.plan.filters.date_field == "start_date" and planned.outcome is None


def test_a_supplied_plan_that_breaks_a_rule_is_a_422_that_lists_the_codes() -> None:
    broken = plan(analysis={"kind": "relate", "x": "enrollment", "y": "enrollment", "color_by": None})
    with pytest.raises(InvalidRequest) as raised:
        service(None).accept(AnalysisRequest(plan=broken), TODAY)
    [error] = errors_of(raised.value)
    assert (error["path"], error["code"]) == ("/plan/analysis/y", "relate_same_measure")


def test_a_supplied_clarification_is_answered_as_one() -> None:
    asking = plan(
        entities=[], analysis={"kind": "clarify", "reason": "missing_entity", "missing": ["condition"]}
    )
    planned = service(None).accept(AnalysisRequest(plan=asking), TODAY)
    assert planned.outcome is not None and planned.outcome.kind == "clarification"


async def test_a_canary_in_the_models_free_text_stays_out_of_the_outcome_message() -> None:
    canary = "CANARY-9f3a"
    declined = plan(
        entities=[],
        interpretation=canary,
        analysis={"kind": "unsupported", "category": "other", "reason": canary},
    )
    planned = await produce(service(FakePlanner([declined])))
    assert planned.outcome is not None and canary not in planned.outcome.message
