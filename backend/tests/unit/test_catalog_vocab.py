"""Enum vocabularies, the nine phase buckets and the enrollment bins."""

from itertools import pairwise
from typing import get_args

import pytest

from ctviz.catalog import vocab
from ctviz.contract.plan import InterventionType, SponsorClass, Status, StudyType


def test_the_plan_literals_and_the_vocabularies_hold_the_same_tokens() -> None:
    assert set(vocab.overall_status().tokens) == set(get_args(Status))
    assert set(vocab.study_type().tokens) == set(get_args(StudyType))
    assert set(vocab.sponsor_class().tokens) == set(get_args(SponsorClass))
    assert set(vocab.intervention_type().tokens) == set(get_args(InterventionType))


def test_labels_come_from_the_registry_snapshot() -> None:
    assert vocab.overall_status().labels["ACTIVE_NOT_RECRUITING"] == "Active, not recruiting"
    assert vocab.masking().labels["NONE"] == "None (Open Label)"
    assert vocab.primary_purpose().labels["ECT"] == "Educational/Counseling/Training"
    assert vocab.sponsor_class().labels["FED"] == "Other U.S. federal"


def test_statuses_run_along_a_trials_life() -> None:
    tokens = vocab.overall_status().tokens
    assert tokens[:5] == (
        "NOT_YET_RECRUITING",
        "RECRUITING",
        "ENROLLING_BY_INVITATION",
        "ACTIVE_NOT_RECRUITING",
        "COMPLETED",
    )
    assert len(tokens) == 14


def test_the_phase_buckets_are_the_nine_expressions_of_the_plan() -> None:
    assert [(bucket.key, bucket.expr) for bucket in vocab.PHASE_BUCKETS] == [
        ("EARLY_PHASE1", "AREA[Phase]EARLY_PHASE1"),
        ("PHASE1", "AREA[Phase](PHASE1 AND NOT PHASE2)"),
        ("PHASE1_PHASE2", "AREA[Phase](PHASE1 AND PHASE2)"),
        ("PHASE2", "AREA[Phase](PHASE2 AND NOT PHASE1 AND NOT PHASE3)"),
        ("PHASE2_PHASE3", "AREA[Phase](PHASE2 AND PHASE3)"),
        ("PHASE3", "AREA[Phase](PHASE3 AND NOT PHASE2)"),
        ("PHASE4", "AREA[Phase]PHASE4"),
        ("NA", "AREA[Phase]NA"),
        ("NONE", "AREA[Phase]MISSING"),
    ]
    assert vocab.PHASE_LABELS["PHASE1_PHASE2"] == "Phase 1/Phase 2"


def test_the_sex_bucket_all_is_quoted_so_it_is_not_the_operator() -> None:
    buckets = {bucket.key: bucket for bucket in vocab.sex().buckets(with_missing=True)}
    assert buckets["ALL"].expr == 'AREA[Sex]"ALL"'
    assert buckets["MISSING"].expr == "AREA[Sex]MISSING"
    assert buckets["MISSING"].label == "Not provided"


def test_has_results_buckets_use_the_lower_case_tokens() -> None:
    assert [(b.key, b.expr) for b in vocab.has_results().buckets()] == [
        ("true", "AREA[HasResults]true"),
        ("false", "AREA[HasResults]false"),
    ]


def test_the_enrollment_bins_are_contiguous_and_cover_every_count() -> None:
    bins = vocab.ENROLLMENT_BINS
    labels = [
        "0",
        "1-9",
        "10-24",
        "25-49",
        "50-99",
        "100-249",
        "250-499",
        "500-999",
        "1000-4999",
        "5000-9999",
        "10000+",
    ]
    assert [b.label for b in bins] == labels
    assert all(left.end == right.start for left, right in pairwise(bins))
    assert bins[-1].end is None


@pytest.mark.parametrize(
    ("count", "label"),
    [
        (0, "0"),
        (1, "1-9"),
        (9, "1-9"),
        (10, "10-24"),
        (99, "50-99"),
        (100, "100-249"),
        (9999, "5000-9999"),
        (10000, "10000+"),
    ],
)
def test_a_count_falls_in_exactly_one_bin(count: int, label: str) -> None:
    assert [b.label for b in vocab.ENROLLMENT_BINS if b.holds(count)] == [label]


def test_bin_expressions_are_inclusive_ranges() -> None:
    expressions = {b.label: b.bucket().expr for b in vocab.ENROLLMENT_BINS}
    assert expressions["0"] == "AREA[EnrollmentCount]RANGE[0,0]"
    assert expressions["100-249"] == "AREA[EnrollmentCount]RANGE[100,249]"
    assert expressions["10000+"] == "AREA[EnrollmentCount]RANGE[10000,MAX]"
