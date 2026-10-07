"""Enum codes, labels and natural orders; the nine exclusive phase buckets; the enrollment bins.

Labels are the registry's own display names from the committed snapshot `data/enums.json`
(`scripts/snapshot_enums.py`); the endpoint has none for sponsor classes, so those are written here.
The orders are the ones a reader expects (phases from early to late, statuses along a trial's life).
Citations always quote the raw token, never the label.
"""

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Final, get_args

from ctviz.catalog.fields import Bucket
from ctviz.contract.plan import InterventionType
from ctviz.ctgov.essie import all_of, area, missing, range_

ENUMS_FILE: Final = Path(__file__).parent / "data" / "enums.json"

SPONSOR_CLASS_LABELS: Final = {
    "INDUSTRY": "Industry",
    "NIH": "NIH",
    "FED": "Other U.S. federal",
    "OTHER_GOV": "Other government",
    "NETWORK": "Network",
    "INDIV": "Individual",
    "OTHER": "Other (academic, hospital and similar)",
    "AMBIG": "Ambiguous",
    "UNKNOWN": "Unknown",
}

NOT_PROVIDED_KEY: Final = "MISSING"
NOT_PROVIDED_LABEL: Final = "Not provided"


@dataclass(frozen=True)
class Vocabulary:
    """A closed list of enum tokens: the area they live in, their natural order and their labels."""

    piece: str  # the API area, `AREA[<piece>]token`
    tokens: tuple[str, ...]  # natural order
    labels: dict[str, str]

    @property
    def not_provided(self) -> Bucket:
        """Where the trials that have no value in this area go."""
        return Bucket(NOT_PROVIDED_KEY, NOT_PROVIDED_LABEL, missing(self.piece))

    def buckets(self, *, with_missing: bool = False) -> tuple[Bucket, ...]:
        """One bucket per token, selected by `AREA[piece]token`; a last "Not provided" bucket on request."""
        buckets = tuple(Bucket(token, self.labels[token], area(self.piece, token)) for token in self.tokens)
        return (*buckets, self.not_provided) if with_missing else buckets


@cache
def _snapshot() -> dict[str, dict[str, str]]:
    snapshot: dict[str, dict[str, str]] = json.loads(ENUMS_FILE.read_text(encoding="utf-8"))
    return snapshot


def _vocabulary(enum_type: str, piece: str, order: tuple[str, ...] = ()) -> Vocabulary:
    labels = _snapshot()[enum_type]
    tokens = order or tuple(labels)
    unknown = set(tokens) - set(labels)
    if unknown:
        raise ValueError(f"Not in the {enum_type} snapshot: {sorted(unknown)}")
    return Vocabulary(piece, tokens, {token: labels[token] for token in tokens})


@cache
def overall_status() -> Vocabulary:
    return _vocabulary(
        "Status",
        "OverallStatus",
        (
            "NOT_YET_RECRUITING",
            "RECRUITING",
            "ENROLLING_BY_INVITATION",
            "ACTIVE_NOT_RECRUITING",
            "COMPLETED",
            "SUSPENDED",
            "TERMINATED",
            "WITHDRAWN",
            "UNKNOWN",
            "AVAILABLE",
            "NO_LONGER_AVAILABLE",
            "TEMPORARILY_NOT_AVAILABLE",
            "APPROVED_FOR_MARKETING",
            "WITHHELD",
        ),
    )


@cache
def study_type() -> Vocabulary:
    return _vocabulary("StudyType", "StudyType", ("INTERVENTIONAL", "OBSERVATIONAL", "EXPANDED_ACCESS"))


@cache
def sponsor_class() -> Vocabulary:
    return Vocabulary("LeadSponsorClass", tuple(SPONSOR_CLASS_LABELS), dict(SPONSOR_CLASS_LABELS))


@cache
def intervention_type() -> Vocabulary:
    return _vocabulary("InterventionType", "InterventionType", tuple(sorted(get_args(InterventionType))))


@cache
def sex() -> Vocabulary:
    return _vocabulary("Sex", "Sex", ("FEMALE", "MALE", "ALL"))


@cache
def age_group() -> Vocabulary:
    return _vocabulary("StandardAge", "StdAge", ("CHILD", "ADULT", "OLDER_ADULT"))


@cache
def allocation() -> Vocabulary:
    return _vocabulary("DesignAllocation", "DesignAllocation", ("RANDOMIZED", "NON_RANDOMIZED", "NA"))


@cache
def masking() -> Vocabulary:
    return _vocabulary("DesignMasking", "DesignMasking", ("NONE", "SINGLE", "DOUBLE", "TRIPLE", "QUADRUPLE"))


@cache
def intervention_model() -> Vocabulary:
    return _vocabulary(
        "InterventionalAssignment",
        "DesignInterventionModel",
        ("PARALLEL", "CROSSOVER", "FACTORIAL", "SEQUENTIAL", "SINGLE_GROUP"),
    )


@cache
def primary_purpose() -> Vocabulary:
    return _vocabulary("PrimaryPurpose", "DesignPrimaryPurpose")


@cache
def has_results() -> Vocabulary:
    return Vocabulary("HasResults", ("true", "false"), {"true": "With results", "false": "Without results"})


# `AREA[Phase]X` means "lists X", so naive phase buckets overlap: a trial lists one phase or two. These
# nine partition any set of trials, and each expression selects exactly its bucket's trials (measured:
# 51, 573, 473, 1259, 43, 324, 19, 54 and 175 for the 2,971 pembrolizumab trials).
PHASE_PIECE: Final = "Phase"
NO_PHASE_KEY: Final = "NONE"
OTHER_PHASE_KEY: Final = "OTHER_COMBINATION"  # a phase array that does not occur in the registry today
OTHER_PHASE_LABEL: Final = "Other combination"
PHASE_BUCKETS: Final = (
    Bucket("EARLY_PHASE1", "Early Phase 1", area(PHASE_PIECE, "EARLY_PHASE1")),
    Bucket("PHASE1", "Phase 1", all_of(PHASE_PIECE, ["PHASE1"], but_not=["PHASE2"])),
    Bucket("PHASE1_PHASE2", "Phase 1/Phase 2", all_of(PHASE_PIECE, ["PHASE1", "PHASE2"])),
    Bucket("PHASE2", "Phase 2", all_of(PHASE_PIECE, ["PHASE2"], but_not=["PHASE1", "PHASE3"])),
    Bucket("PHASE2_PHASE3", "Phase 2/Phase 3", all_of(PHASE_PIECE, ["PHASE2", "PHASE3"])),
    Bucket("PHASE3", "Phase 3", all_of(PHASE_PIECE, ["PHASE3"], but_not=["PHASE2"])),
    Bucket("PHASE4", "Phase 4", area(PHASE_PIECE, "PHASE4")),
    Bucket("NA", "Not Applicable", area(PHASE_PIECE, "NA")),
    Bucket(NO_PHASE_KEY, "No phase listed", missing(PHASE_PIECE)),
)
PHASE_LABELS: Final = {bucket.key: bucket.label for bucket in PHASE_BUCKETS}
# The phases a record lists, as a set, to the bucket of that combination.
PHASE_COMBINATIONS: Final = {
    frozenset({"EARLY_PHASE1"}): "EARLY_PHASE1",
    frozenset({"PHASE1"}): "PHASE1",
    frozenset({"PHASE1", "PHASE2"}): "PHASE1_PHASE2",
    frozenset({"PHASE2"}): "PHASE2",
    frozenset({"PHASE2", "PHASE3"}): "PHASE2_PHASE3",
    frozenset({"PHASE3"}): "PHASE3",
    frozenset({"PHASE4"}): "PHASE4",
    frozenset({"NA"}): "NA",
}


# The phase tokens each bucket stands for, to tell which buckets a phase filter can leave non-empty.
PHASE_TOKENS_OF: Final = {key: tokens for tokens, key in PHASE_COMBINATIONS.items()}


@dataclass(frozen=True)
class EnrollmentBin:
    """Fixed, roughly logarithmic: equal widths would put two thirds of the trials in the first bin."""

    start: int
    end: int | None  # exclusive, as in the contract's half-open bins; None for the last bin

    @property
    def label(self) -> str:
        if self.end is None:
            return f"{self.start}+"
        return str(self.start) if self.end == self.start + 1 else f"{self.start}-{self.end - 1}"

    def bucket(self) -> Bucket:
        """The server ranges are inclusive at both ends, so the exclusive end steps back by one."""
        return Bucket(
            self.label,
            self.label,
            range_("EnrollmentCount", self.start, None if self.end is None else self.end - 1),
        )

    def holds(self, count: int) -> bool:
        return count >= self.start and (self.end is None or count < self.end)


_BIN_STARTS: Final = (0, 1, 10, 25, 50, 100, 250, 500, 1000, 5000, 10000)
ENROLLMENT_BINS: Final = tuple(
    EnrollmentBin(start, end) for start, end in zip(_BIN_STARTS, (*_BIN_STARTS[1:], None), strict=True)
)
