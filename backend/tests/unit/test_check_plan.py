"""`check_plan`: one triggering plan per implemented rule, and the order of execution."""

from typing import Any

import pytest

from ctviz.catalog.countries import load_country_table
from ctviz.contract.plan import Aggregate, Clarify, Network, QueryPlan, Relate, TrialList
from ctviz.planning.grounding import Mode
from ctviz.planning.validate import PlanCheck, check_plan
from tests.unit.plan_samples import TODAY, Countries, aggregate, entity, plan, request

PEMBRO = entity("drug", "pembrolizumab")


def check(candidate: QueryPlan, query: Any = None, *, mode: Mode = "model", **kwargs: Any) -> PlanCheck:
    req = request() if query is None else query
    return check_plan(candidate, req, mode=mode, countries=Countries(), today=TODAY, **kwargs)


def codes(result: PlanCheck) -> set[str]:
    return {adjustment.code for adjustment in result.adjustments}


def evidence(family: str, phrase: str) -> dict[str, str]:
    return {"family": family, "phrase": phrase}


# --- the plain case --------------------------------------------------------------------------------


def test_a_grounded_plan_passes_and_gets_its_defaults() -> None:
    result = check(plan())
    assert result.blocking == () and result.outcome is None and result.adjustments == ()
    analysis = result.plan.analysis
    assert isinstance(analysis, Aggregate) and analysis.top_n == 15
    assert result.plan.filters.date_field == "start_date"


def test_a_date_axis_gets_year_and_its_own_date_field() -> None:
    result = check(plan(analysis=aggregate("completion_date")))
    analysis = result.plan.analysis
    assert isinstance(analysis, Aggregate) and analysis.time_unit == "year" and analysis.top_n is None
    assert result.plan.filters.date_field == "completion_date"


def test_defaults_for_networks_and_lists() -> None:
    drugs = {"kind": "network", "source": "drug", "target": "drug", "link": None}
    sponsors = {"kind": "network", "source": "sponsor", "target": "drug", "link": None}
    listing = {"kind": "trial_list", "sort_by": "enrollment", "order": "desc", "limit": None}
    assert isinstance(a := check(plan(analysis=drugs)).plan.analysis, Network) and a.link == "same_arm"
    assert isinstance(a := check(plan(analysis=sponsors)).plan.analysis, Network) and a.link == "same_trial"
    assert isinstance(a := check(plan(analysis=listing)).plan.analysis, TrialList) and a.limit == 10


# --- rule 1 ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["x" * 301, "Counts NCT01234567 by phase.", "Counts 4242 trials by phase."],
    ids=["too long", "trial id", "number nobody wrote"],
)
def test_rule_1_text_hygiene_replaces_the_text(text: str) -> None:
    result = check(plan(interpretation=text))
    assert "text_hygiene" in codes(result)
    assert result.plan.interpretation.startswith("The planner's reading")


def test_rule_1_keeps_a_number_the_question_wrote() -> None:
    result = check(
        plan(interpretation="Count trials since 2015."), request("Trials since 2015 for pembrolizumab")
    )
    assert "text_hygiene" not in codes(result)


def test_rule_1_covers_the_reason_of_unsupported() -> None:
    unsupported = {"kind": "unsupported", "category": "other", "reason": "See NCT01234567."}
    result = check(plan(analysis=unsupported))
    assert "text_hygiene" in codes(result)
    assert result.outcome is not None and result.outcome.kind == "unsupported"


# --- rules 3 to 5: structured fields win -------------------------------------------------------------


def test_rule_4_the_field_replaces_a_placeholder_entity() -> None:
    result = check(
        plan(entities=[entity("drug", "this drug")]),
        request("How has the number of trials for this drug changed over time?", drug_name="Pembrolizumab"),
    )
    assert [e.value for e in result.plan.entities] == ["Pembrolizumab"]
    assert result.outcome is None and "field_override" in codes(result)


def test_rule_4_a_field_equal_to_the_planners_entity_is_no_conflict() -> None:
    result = check(plan(), request("Phases for pembrolizumab", drug_name="Pembrolizumab"))
    assert "field_override" not in codes(result) and len(result.plan.entities) == 1


def test_rule_4_the_request_filters_and_years_win() -> None:
    model = plan(
        filters={"phases": ["PHASE1"], "evidence": [evidence("phases", "phase 1")], "year_from": 2015}
    )
    result = check(
        model, request("Phase 1 pembrolizumab trials since 2015", trial_phase=["PHASE3"], start_year=2020)
    )
    assert result.plan.filters.phases == ["PHASE3"] and result.plan.filters.year_from == 2020
    assert result.plan.filters.evidence == [] and result.blocking == ()


def test_rule_4_a_request_comparison_replaces_the_planners() -> None:
    model = plan(
        entities=[entity("drug", "pembrolizumab", "compare"), entity("drug", "nivolumab", "compare")]
    )
    result = check(
        model,
        request(
            "Compare pembrolizumab vs nivolumab",
            compare={"field": "drug_name", "values": ["Keytruda", "Opdivo"]},
        ),
    )
    assert [(e.value, e.role) for e in result.plan.entities] == [
        ("Keytruda", "compare"),
        ("Opdivo", "compare"),
    ]


def test_rule_4_a_field_equal_to_a_compared_value_is_not_an_extra_filter() -> None:
    model = plan(
        entities=[entity("drug", "pembrolizumab", "compare"), entity("drug", "nivolumab", "compare")]
    )
    result = check(model, request("Compare pembrolizumab vs nivolumab", drug_name="pembrolizumab"))
    assert [e.role for e in result.plan.entities] == ["compare", "compare"]


def test_rule_4_a_clarify_that_asks_for_what_the_request_supplies_is_blocking() -> None:
    asking = {"kind": "clarify", "reason": "missing_entity", "missing": ["drug_name"]}
    result = check(
        plan(entities=[], analysis=asking), request("Trials for this drug", drug_name="Pembrolizumab")
    )
    assert [issue.code for issue in result.blocking] == ["field_override"] and result.outcome is None


def test_rule_5_hints_replace_the_planners_choices() -> None:
    model = plan(analysis=aggregate("phase", top_n=5), chart_preference="table")
    result = check(
        model,
        request(
            "Pembrolizumab trials, five of them",
            group_by=["start_date", "phase"],
            time_unit="quarter",
            top_n=7,
            chart_type="time_series",
        ),
    )
    assert result.plan.analysis == Aggregate(
        kind="aggregate", dimension="start_date", series="phase", time_unit="quarter", top_n=7
    )
    assert result.plan.chart_preference == "time_series" and "hint_override" in codes(result)


def test_rule_5_a_total_becomes_an_aggregate_when_group_by_is_given() -> None:
    result = check(plan(analysis={"kind": "total"}), request("pembrolizumab trials", group_by=["phase"]))
    assert isinstance(result.plan.analysis, Aggregate) and result.plan.analysis.dimension == "phase"


def test_rule_5_top_n_from_the_request_becomes_the_limit_of_a_list() -> None:
    listing = {"kind": "trial_list", "sort_by": "enrollment", "order": "desc", "limit": None}
    result = check(plan(analysis=listing), request("pembrolizumab trials by size", top_n=12))
    assert isinstance(result.plan.analysis, TrialList) and result.plan.analysis.limit == 12


def test_rule_3_a_duplicate_entity_is_kept_once() -> None:
    result = check(plan(entities=[PEMBRO, entity("drug", "Pembrolizumab")]))
    assert len(result.plan.entities) == 1 and "duplicate_entity" in codes(result)


# --- rules 24 to 27: direct outcomes ---------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["Drug A", "[condition]", "this drug", "two conditions", "<drug name>", "the condition"],
)
def test_rule_24_a_placeholder_entity_is_a_clarification(value: str) -> None:
    result = check(
        plan(entities=[entity("condition", value)]), request("Compare phases for Drug A vs Drug B")
    )
    assert result.outcome is not None and result.outcome.kind == "clarification"
    assert result.outcome.reason == "missing_entity"
    assert isinstance(result.plan.analysis, Clarify) and result.plan.analysis.missing == ["condition"]
    assert result.plan.entities == [] and "placeholder_entity" in codes(result)


def test_rule_24_a_placeholder_from_a_field_is_the_clients_choice() -> None:
    result = check(plan(entities=[]), request("Phases", drug_name="Drug A"))
    assert result.outcome is None


def test_rule_25_an_unknown_country_is_a_clarification_with_close_matches() -> None:
    result = check(plan(entities=[entity("country", "Fraance")]), request("Trials in Fraance"), mode="model")
    assert result.outcome is not None and result.outcome.reason == "unknown_value"
    assert result.outcome.clarification is not None
    options = result.outcome.clarification.options
    assert [option.request.country for option in options] == [["France"]]


def test_rule_25_works_with_the_real_country_table() -> None:
    table = load_country_table()
    known = check_plan(
        plan(entities=[entity("country", "USA")]),
        request("Trials in USA"),
        mode="model",
        countries=table,
        today=TODAY,
    )
    unknown = check_plan(
        plan(entities=[entity("country", "Fraance")]),
        request("Trials in Fraance"),
        mode="model",
        countries=table,
        today=TODAY,
    )
    assert known.outcome is None
    assert unknown.outcome is not None and unknown.outcome.clarification is not None
    assert [option.request.country for option in unknown.outcome.clarification.options] == [["France"]]


def test_rule_25_in_structured_mode_is_a_blocking_issue_with_the_suggestions() -> None:
    model = plan(entities=[entity("country", "Fraance")])
    result = check_plan(model, request(), mode="supplied", countries=Countries(), today=TODAY)
    assert [(i.code, i.path) for i in result.blocking] == [("unknown_country", "/entities/0/value")]
    assert result.blocking[0].allowed == ("France",)


@pytest.mark.parametrize("question", ["phase 5 trials of pembrolizumab", "Phase 0 pembrolizumab", "phase VI"])
def test_rule_26_a_phase_outside_one_to_four_is_a_clarification(question: str) -> None:
    result = check(plan(), request(question))
    assert result.outcome is not None and result.outcome.reason == "unknown_value"


@pytest.mark.parametrize("question", ["phase 2/3 pembrolizumab", "Phase IV pembrolizumab", "a phase in time"])
def test_rule_26_leaves_valid_mentions_alone(question: str) -> None:
    assert check(plan(), request(question)).outcome is None


def test_rule_27_an_nct_id_entity_is_unsupported() -> None:
    result = check(plan(entities=[entity("term", "NCT01234567")]), request("Phases for NCT01234567"))
    assert result.outcome is not None and result.outcome.kind == "unsupported"
    assert result.outcome.reason == "single_trial_lookup"


def test_a_plan_that_says_clarify_or_unsupported_is_an_outcome() -> None:
    asking = {"kind": "clarify", "reason": "ambiguous_request", "missing": []}
    declined = {
        "kind": "unsupported",
        "category": "needs_data_not_in_registry",
        "reason": "No efficacy data.",
    }
    first = check(plan(entities=[], analysis=asking))
    second = check(plan(entities=[], analysis=declined))
    assert first.outcome is not None and first.outcome.clarification is not None
    assert first.outcome.clarification.reason == "ambiguous_request"
    assert second.outcome is not None and second.outcome.reason == "needs_data_not_in_registry"


# --- the fixes: rules 2 and 6 to 17 ------------------------------------------------------------------


def test_rule_2_a_filter_listing_every_value_is_emptied() -> None:
    every = ["EARLY_PHASE1", "PHASE1", "PHASE2", "PHASE3", "PHASE4", "NA"]
    model = plan(filters={"phases": every, "evidence": [evidence("phases", "every phase")]})
    result = check(model, request("pembrolizumab trials in every phase"))
    assert result.plan.filters.phases == [] and result.plan.filters.evidence == []
    assert result.blocking == ()


def test_rule_6_an_entity_with_nothing_to_search_for_is_dropped_with_a_warning() -> None:
    result = check(plan(entities=[PEMBRO, entity("term", "***")]), request("pembrolizumab *** phases"))
    assert [e.value for e in result.plan.entities] == ["pembrolizumab"]
    assert [w.code for w in result.warnings] == ["empty_entity"]


def test_rule_7_inverted_years_are_swapped() -> None:
    model = plan(filters={"year_from": 2020, "year_to": 2015})
    result = check(model, request("pembrolizumab trials from 2020 to 2015"))
    assert (result.plan.filters.year_from, result.plan.filters.year_to) == (2015, 2020)


def test_rule_8_one_compared_entity_becomes_a_filter() -> None:
    result = check(plan(entities=[entity("drug", "pembrolizumab", "compare")]))
    assert [e.role for e in result.plan.entities] == ["filter"]


def test_rule_9_only_four_compared_entities_are_kept() -> None:
    names = ["pembrolizumab", "nivolumab", "atezolizumab", "durvalumab", "ipilimumab"]
    model = plan(entities=[entity("drug", n, "compare") for n in names])
    result = check(model, request("Compare " + " vs ".join(names)))
    assert [e.value for e in result.plan.entities] == names[:4]
    assert [w.code for w in result.warnings] == ["compare_truncated"]


def test_rule_10_a_series_is_dropped_while_entities_are_compared() -> None:
    model = plan(
        entities=[entity("drug", "pembrolizumab", "compare"), entity("drug", "nivolumab", "compare")],
        analysis=aggregate("phase", series="sponsor_class"),
    )
    result = check(model, request("Compare pembrolizumab vs nivolumab by phase and sponsor class"))
    assert isinstance(result.plan.analysis, Aggregate) and result.plan.analysis.series is None
    assert "series_dropped" in {w.code for w in result.warnings}


def test_rule_11_a_series_equal_to_the_dimension_is_dropped() -> None:
    result = check(plan(analysis=aggregate("phase", series="phase")))
    assert isinstance(result.plan.analysis, Aggregate) and result.plan.analysis.series is None


def test_rule_11_a_multi_valued_colour_is_dropped() -> None:
    relate = {"kind": "relate", "x": "enrollment", "y": "duration_months", "color_by": "intervention_type"}
    result = check(plan(analysis=relate))
    assert isinstance(result.plan.analysis, Relate) and result.plan.analysis.color_by is None


def test_rule_12_a_time_unit_on_a_category_is_dropped() -> None:
    result = check(plan(analysis=aggregate("phase", time_unit="year")))
    assert isinstance(result.plan.analysis, Aggregate) and result.plan.analysis.time_unit is None


def test_rule_13_same_arm_applies_to_drug_pairs_only() -> None:
    link = {"kind": "network", "source": "sponsor", "target": "drug", "link": "same_arm"}
    result = check(plan(analysis=link))
    assert isinstance(result.plan.analysis, Network) and result.plan.analysis.link == "same_trial"


def test_rules_14_and_15_a_grounded_number_is_clamped() -> None:
    result = check(
        plan(analysis=aggregate("sponsor", top_n=100)), request("The top 100 sponsors of pembrolizumab")
    )
    assert isinstance(result.plan.analysis, Aggregate) and result.plan.analysis.top_n == 50
    assert codes(result) == {"limit_clamped"}


@pytest.mark.parametrize(
    ("question", "kept"), [("the top ten sponsors of pembrolizumab", 10), ("sponsors", None)]
)
def test_rule_15_a_number_must_be_in_the_question(question: str, kept: int | None) -> None:
    result = check(plan(analysis=aggregate("sponsor", top_n=10)), request(question + " pembrolizumab"))
    analysis = result.plan.analysis
    assert isinstance(analysis, Aggregate) and analysis.top_n == (kept or 15)
    assert ("ungrounded_number" in codes(result)) is (kept is None)


def test_rule_15_a_number_from_the_request_field_is_grounded() -> None:
    result = check(
        plan(analysis=aggregate("sponsor", top_n=9)), request("sponsors of pembrolizumab", top_n=9)
    )
    assert "ungrounded_number" not in codes(result)


def test_rule_16_an_incompatible_preference_is_ignored_with_a_warning() -> None:
    result = check(plan(chart_preference="network_graph"))
    assert result.plan.chart_preference is None and [w.code for w in result.warnings] == [
        "chart_preference_ignored"
    ]


@pytest.mark.parametrize(
    ("dimension", "preference"),
    [("phase", "table"), ("start_date", "bar_chart"), ("enrollment", "histogram")],
)
def test_rule_16_keeps_a_compatible_preference(dimension: str, preference: str) -> None:
    result = check(plan(analysis=aggregate(dimension), chart_preference=preference))
    assert result.plan.chart_preference == preference


def test_rule_17_a_phase_its_phrase_does_not_state_is_dropped() -> None:
    model = plan(
        filters={
            "phases": ["PHASE2", "PHASE3"],
            "evidence": [evidence("phases", "phase 3")],
        }
    )
    result = check(model, request("phase 3 pembrolizumab trials"))
    assert result.plan.filters.phases == ["PHASE3"] and "ungrounded_filter_value" in codes(result)


@pytest.mark.parametrize(
    ("phase", "phrase"),
    [
        ("PHASE3", "late-stage"),
        ("PHASE1", "first-in-human"),
        ("EARLY_PHASE1", "early phase 1"),
        ("PHASE2", "phase II"),
    ],
)
def test_rule_17_accepts_the_keywords_and_numerals(phase: str, phrase: str) -> None:
    model = plan(filters={"phases": [phase], "evidence": [evidence("phases", phrase)]})
    result = check(model, request(f"{phrase} pembrolizumab trials"))
    assert result.plan.filters.phases == [phase] and result.blocking == ()


# --- the blocking rules: 18 to 23 ---------------------------------------------------------------------


def test_rule_18_an_entity_not_in_the_question_is_blocking() -> None:
    result = check(plan(entities=[entity("drug", "Keytruda")]), request("Phases for pembrolizumab"))
    assert [(i.code, i.path) for i in result.blocking] == [("ungrounded_entity", "/entities/0/value")]


def test_rule_18_coordinated_entities_are_grounded_by_tokens() -> None:
    result = check(
        plan(entities=[entity("condition", "breast cancer")]), request("Trials for breast and lung cancer")
    )
    assert result.blocking == ()


@pytest.mark.parametrize(
    ("question", "year", "blocked"),
    [
        ("pembrolizumab trials since 2015", 2015, False),
        ("pembrolizumab trials", 2015, True),
        ("pembrolizumab trials in the last five years", 2021, False),
        ("pembrolizumab trials in the last five years", 1960, True),
    ],
)
def test_rule_19_a_year_must_be_written_or_relative(question: str, year: int, blocked: bool) -> None:
    result = check(plan(filters={"year_from": year}), request(question))
    assert bool(result.blocking) is blocked
    assert ("relative_year" in codes(result)) is ("last five" in question and not blocked)


def test_rule_20_a_filter_without_a_quoted_phrase_is_blocking() -> None:
    model = plan(filters={"statuses": ["RECRUITING"], "evidence": [evidence("statuses", "halted")]})
    result = check(model, request("pembrolizumab trials"))
    assert [(i.code, i.path) for i in result.blocking] == [("ungrounded_filter", "/filters/statuses")]


def test_rule_20_a_field_counts_as_evidence() -> None:
    result = check(
        plan(filters={"statuses": ["RECRUITING"]}), request("pembrolizumab trials", status=["RECRUITING"])
    )
    assert result.blocking == ()


def test_rule_21_compared_entities_of_two_kinds_are_blocking() -> None:
    model = plan(
        entities=[entity("drug", "pembrolizumab", "compare"), entity("condition", "asthma", "compare")]
    )
    result = check(model, request("Compare pembrolizumab vs asthma"))
    assert [i.code for i in result.blocking] == ["mixed_compare_kinds"]


def test_rule_22_x_equal_to_y_is_blocking_in_model_and_supplied_modes() -> None:
    relate = {"kind": "relate", "x": "enrollment", "y": "enrollment", "color_by": None}
    assert [i.code for i in check(plan(analysis=relate)).blocking] == ["relate_same_measure"]
    supplied = check_plan(plan(analysis=relate), None, mode="supplied", countries=None, today=TODAY)
    assert [i.code for i in supplied.blocking] == ["relate_same_measure"]


@pytest.mark.parametrize(
    "model",
    [
        plan(analysis={"kind": "network", "source": "sponsor", "target": "sponsor", "link": None}),
        plan(
            entities=[entity("drug", "pembrolizumab", "compare"), entity("drug", "nivolumab", "compare")],
            analysis={"kind": "network", "source": "sponsor", "target": "drug", "link": None},
        ),
    ],
    ids=["one sponsor per trial", "network with compared names"],
)
def test_rule_23_unsupported_network_pairs_are_blocking(model: QueryPlan) -> None:
    result = check(model, request("pembrolizumab nivolumab sponsors network"))
    assert [i.code for i in result.blocking] == ["network_pair_unsupported"]


# --- after the repair turn: rules 28 and 29 -------------------------------------------------------------


def test_rule_28_an_ungrounded_filter_is_dropped_after_the_repair() -> None:
    model = plan(filters={"statuses": ["RECRUITING"], "evidence": [evidence("statuses", "halted")]})
    result = check(model, request("pembrolizumab trials"), after_repair=True)
    assert result.plan.filters.statuses == [] and result.blocking == () and result.outcome is None
    assert [w.code for w in result.warnings] == ["filter_dropped"]


def test_rule_29_another_blocking_issue_becomes_could_not_interpret() -> None:
    result = check(
        plan(entities=[entity("drug", "Keytruda")]), request("Phases for pembrolizumab"), after_repair=True
    )
    assert result.blocking == () and result.outcome is not None
    assert result.outcome.reason == "could_not_interpret"
    assert [w.code for w in result.warnings] == ["plan_not_repaired"]


def test_rule_29_an_unsupported_network_pair_becomes_unsupported() -> None:
    model = plan(analysis={"kind": "network", "source": "sponsor", "target": "sponsor", "link": None})
    result = check(model, request("pembrolizumab sponsors"), after_repair=True)
    assert result.outcome is not None and result.outcome.kind == "unsupported"
    assert result.outcome.reason == "analysis_not_supported"


# --- structured and supplied modes ----------------------------------------------------------------------


def test_grounding_does_not_run_for_supplied_plans() -> None:
    model = plan(
        entities=[entity("drug", "Keytruda")],
        filters={"statuses": ["RECRUITING"], "year_from": 2015},
        interpretation="x" * 400,
    )
    result = check_plan(model, None, mode="supplied", countries=None, today=TODAY)
    assert result.blocking == () and result.outcome is None
    assert result.plan.interpretation == "x" * 400


def test_a_supplied_nct_id_entity_is_blocking() -> None:
    model = plan(entities=[entity("term", "NCT01234567")])
    result = check_plan(model, None, mode="supplied", countries=None, today=TODAY)
    assert [i.code for i in result.blocking] == ["nct_id_entity"]
