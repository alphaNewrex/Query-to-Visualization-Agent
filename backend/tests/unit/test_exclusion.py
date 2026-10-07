"""Leaving out: the request field, the plan checks, the expression, the echo and the sentences."""

import dataclasses

from ctviz.applied import applied_filters
from ctviz.contract.plan import Entity
from ctviz.contract.request import QueryRequest
from ctviz.ctgov import essie
from ctviz.engine.resolve import bind_excluded
from ctviz.planning.structured import plan_from_fields
from ctviz.viz import text
from tests.unit.engine_support import scope
from tests.unit.plan_samples import entity, plan, request
from tests.unit.test_check_plan import check, codes, evidence

DIABETES = entity("condition", "diabetes", "exclude")


def _resolution(
    definition: str, kind: str = "condition", text: str = "diabetes", term: str = "diabetes"
) -> object:
    from ctviz.contract.response import EntityResolution

    return EntityResolution(
        kind=kind,  # type: ignore[arg-type]
        planned_kind=kind,  # type: ignore[arg-type]
        source="question",
        text=text,
        term_searched=term,
        definition=definition,  # type: ignore[arg-type]
        status="ok",
        trials_matched=10,
        strict_name_matches=None,
        condition_name_matches=None,
        registry_term=None,
        other_readings=[],
        candidates=[],
    )


def test_an_excluded_condition_is_a_negated_search_of_the_same_area() -> None:
    term = bind_excluded(_resolution("condition_search"))  # type: ignore[arg-type]

    assert term.expr == "NOT (AREA[ConditionSearch](diabetes))"
    assert term.parameter is None


def test_every_definition_has_a_negated_form() -> None:
    forms = {
        "intervention_search": "NOT (AREA[InterventionSearch](diabetes))",
        "lead_sponsor_search": "NOT (AREA[LeadSponsorName](diabetes))",
        "term_search": "NOT (AREA[BasicSearch](diabetes))",
        "intervention_name": 'NOT (AREA[InterventionName]"diabetes")',
    }
    for definition, expected in forms.items():
        assert bind_excluded(_resolution(definition)).expr == expected  # type: ignore[arg-type]
    assert (
        bind_excluded(_resolution("country_exact", "country", "China", "China")).expr  # type: ignore[arg-type]
        == 'NOT (AREA[LocationCountry]"China")'
    )


def test_a_scope_writes_its_exclusions_into_the_one_advanced_expression() -> None:
    base = scope()
    left_out = dataclasses.replace(
        base,
        excluded=(bind_excluded(_resolution("condition_search")),),  # type: ignore[arg-type]
        excluded_statuses=("TERMINATED", "WITHDRAWN"),
    )

    advanced = left_out.params().advanced

    assert advanced == (
        "(NOT (AREA[ConditionSearch](diabetes))) AND (NOT (AREA[OverallStatus](TERMINATED OR WITHDRAWN)))"
    )
    assert essie.search("ConditionSearch", "a b") == "AREA[ConditionSearch](a b)"


def test_the_sentences_say_what_is_left_out() -> None:
    left_out = dataclasses.replace(
        scope(),
        excluded=(bind_excluded(_resolution("condition_search")),),  # type: ignore[arg-type]
        excluded_statuses=("TERMINATED", "WITHDRAWN"),
    )

    assert text.scope_filters(left_out) == ["excluding diabetes", "Status: not Terminated or Withdrawn"]


def test_the_request_field_replaces_what_the_planner_read_and_is_echoed() -> None:
    model = plan(entities=[entity("condition", "obesity"), entity("condition", "diabetes", "exclude")])
    result = check(
        model,
        request(
            "Obesity trials, not involving diabetes or cancer",
            exclude={"condition": ["cancer"], "status": ["WITHDRAWN"]},
        ),
    )

    echoed = applied_filters(result.plan)

    assert [(e.value, e.role) for e in result.plan.entities] == [
        ("obesity", "filter"),
        ("cancer", "exclude"),
    ]
    assert echoed.exclude.condition == ["cancer"] and echoed.exclude.status == ["WITHDRAWN"]
    assert echoed.condition == ["obesity"]
    assert "field_override" in codes(result)


def test_excluded_statuses_need_their_words_in_the_question() -> None:
    model = plan(
        entities=[],
        filters={
            "exclude_statuses": ["TERMINATED"],
            "evidence": [evidence("exclude_statuses", "without cats")],
        },
    )

    result = check(model, request("Pembrolizumab trials, but not those that ended early"))

    assert [issue.code for issue in result.blocking] == ["ungrounded_filter"]
    assert result.blocking[0].path == "/filters/exclude_statuses"


def test_a_grounded_status_exclusion_passes_and_a_stray_evidence_item_is_dropped() -> None:
    good = plan(
        filters={
            "exclude_statuses": ["TERMINATED", "WITHDRAWN"],
            "evidence": [evidence("exclude_statuses", "exclude terminated and withdrawn")],
        }
    )
    stray = plan(filters={"evidence": [evidence("exclude_statuses", "excluding trials")]})
    question = "Pembrolizumab phases, exclude terminated and withdrawn studies, excluding trials"

    assert check(good, request(question)).blocking == ()
    assert "dangling_evidence" in codes(check(stray, request(question)))


def test_leaving_out_what_is_also_required_is_sent_back_to_the_model() -> None:
    both = plan(
        entities=[entity("condition", "diabetes"), DIABETES],
        filters={"statuses": ["RECRUITING"], "exclude_statuses": ["RECRUITING"]},
    )

    result = check(both, request("Recruiting diabetes trials, not diabetes, not recruiting"))

    assert "exclusion_conflict" in {issue.code for issue in result.blocking}


def test_structured_mode_builds_the_exclusions_from_the_fields() -> None:
    fields = QueryRequest.model_validate(
        {
            "query": "Obesity phases",
            "condition": ["obesity"],
            "group_by": ["phase"],
            "exclude": {"condition": ["diabetes"], "status": ["WITHDRAWN"]},
            "options": {"planner": "structured"},
        }
    )

    built = plan_from_fields(fields)

    assert Entity(kind="condition", value="diabetes", role="exclude") in built.entities
    assert built.filters.exclude_statuses == ["WITHDRAWN"]


def test_an_empty_exclude_object_is_no_exclusion() -> None:
    fields = QueryRequest.model_validate(
        {"query": "Obesity phases", "exclude": {"condition": [], "status": []}}
    )

    assert fields.exclude is not None and fields.exclude.is_empty


def test_a_multi_word_exclusion_is_the_exact_phrase_and_a_single_word_is_not_quoted() -> None:
    phrase = bind_excluded(_resolution("condition_search", text="type 2 diabetes", term="type 2 diabetes"))  # type: ignore[arg-type]
    word = bind_excluded(_resolution("condition_search"))  # type: ignore[arg-type]

    assert phrase.expr == 'NOT (AREA[ConditionSearch]"type 2 diabetes")'
    assert word.expr == "NOT (AREA[ConditionSearch](diabetes))"
    assert "whole phrase" in phrase.note and "whole phrase" not in word.note
