"""The types that the catalogue, the engine and the builders share: how they are built, what they refuse."""

from dataclasses import FrozenInstanceError

import pytest

from ctviz.catalog.fields import CATALOG, BoundDimension, Bucket, Evidence, FieldSpec, Value, Window
from ctviz.contract.plan import QueryPlan
from ctviz.contract.response import Outcome
from ctviz.ctgov.params import Scope
from ctviz.engine.frame import Cell, Exclusion, Frame, TrialEvidence
from ctviz.engine.lower import EnginePlan, ListRows, PointRows, Resolved

PLAN = QueryPlan.model_validate(
    {
        "interpretation": "Count trials.",
        "entities": [],
        "filters": {
            "phases": [],
            "statuses": [],
            "study_types": [],
            "sponsor_classes": [],
            "intervention_types": [],
            "exclude_statuses": [],
            "sexes": [],
            "age_groups": [],
            "allocations": [],
            "maskings": [],
            "primary_purposes": [],
            "has_results": [],
            "intervention_models": [],
            "evidence": [],
            "date_field": None,
            "year_from": None,
            "year_to": None,
        },
        "analysis": {"kind": "total", "statistic": None, "of": None},
        "chart_preference": None,
        "unapplied": [],
    }
)
SCOPE = Scope(id="s0", label=None, terms=(), enum_filters={}, date_range=None)
PHASE = FieldSpec(
    key="phase",
    title="Phase",
    kind="category",
    pieces=("Phase", "StudyType"),
    extract=lambda study, context, dimension: [],
    is_exclusive=True,
    is_ordinal=True,
)
AXIS = BoundDimension(spec=PHASE, time_unit=None, role="axis")


def test_a_field_spec_needs_only_what_every_field_has() -> None:
    assert (PHASE.buckets, PHASE.bucket_for, PHASE.missing, PHASE.presence, PHASE.prepare) == (None,) * 5
    assert PHASE.notes == ()


def test_the_catalogue_is_keyed_by_the_key_of_each_spec() -> None:
    assert all(key == spec.key for key, spec in CATALOG.items())


def test_a_value_carries_its_evidence_and_has_no_arm_groups_unless_given() -> None:
    value = Value(
        key="PHASE2",
        label="Phase 2",
        evidence=(Evidence("protocolSection.designModule.phases[0]", "PHASE2"),),
    )

    assert value.groups == frozenset()
    assert Bucket(key="NONE", label="No phase listed", expr=None).expr is None


def test_the_shared_value_types_are_immutable() -> None:
    with pytest.raises(FrozenInstanceError):
        Window(unit="year", first="2015", last="2026").first = "2016"  # type: ignore[misc]


def test_a_frame_made_from_a_scope_and_its_dimensions_has_empty_tables_and_zero_counters() -> None:
    frame = Frame(scope=SCOPE, dims=(AXIS, AXIS), sample_size=5)

    assert len(frame.marginals) == 2
    assert frame.marginals[0] is not frame.marginals[1]
    assert (frame.cells, frame.excluded, frame.subset) == ({}, {}, None)
    assert (frame.matched, frame.seen, frame.analyzed) == (0, 0, 0)


def test_a_frame_without_dimensions_has_no_marginal_table() -> None:
    assert Frame(scope=SCOPE, dims=(), sample_size=5).marginals == ()


def test_two_frames_never_share_a_table() -> None:
    first, second = (
        Frame(scope=SCOPE, dims=(AXIS,), sample_size=5),
        Frame(scope=SCOPE, dims=(AXIS,), sample_size=5),
    )
    first.cells[("PHASE2",)] = Cell(key=("PHASE2",))
    first.excluded["no_start_date"] = Exclusion(message="No start date.")

    assert second.cells == {}
    assert second.excluded == {}


def test_a_cell_counts_trials_and_keeps_its_own_sample() -> None:
    cell, other = Cell(key=("PHASE2",), labels=("Phase 2",)), Cell(key=("PHASE3",))
    cell.sample.append(TrialEvidence("NCT00000001", (), (True, "2020-01-01", "NCT00000001")))

    assert (cell.trials, cell.expr, cell.source_url) == (0, None, None)
    assert other.sample == []


def test_a_better_citation_ranks_higher_than_a_worse_one() -> None:
    named, unnamed = (True, "2015-01-01", "NCT00000001"), (False, "2026-01-01", "NCT00000009")
    newer, older = (True, "2020-05-01", "NCT00000001"), (True, "2020-04-01", "NCT00000009")

    assert named > unnamed
    assert newer > older


def test_the_internal_plan_holds_one_set_of_rows_or_a_grouping_not_both() -> None:
    points = PointRows(x="enrollment", y="duration_months", color=AXIS)
    listing = ListRows(sort_by="enrollment", order="desc")
    plan = EnginePlan(
        scopes=(SCOPE,),
        compare_kind=None,
        dimensions=(),
        relation=None,
        pairing="same_trial",
        rows=points,
        window=None,
        top_n=15,
        max_series=10,
        min_link_weight=2,
        max_links=60,
        chart_preference=None,
        citations_per_datum=5,
        public=PLAN,
    )

    assert plan.rows is points
    assert (listing.sort_by, listing.order) == ("enrollment", "desc")  # how many rows is EnginePlan.top_n


def test_what_the_resolver_hands_on_names_each_scope_it_counted() -> None:
    resolved = Resolved(entities=(), scopes=(SCOPE,), matched={"s0": 2971}, warnings=(), assumptions=())

    assert resolved.matched[resolved.scopes[0].id] == 2971


def test_an_outcome_defaults_to_no_clarification_and_no_warnings() -> None:
    outcome = Outcome(kind="no_data", reason="no_match", message="Nothing matches.")

    assert (outcome.clarification, outcome.warnings) == (None, ())
