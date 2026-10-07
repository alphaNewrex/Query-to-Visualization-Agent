"""The types of the field catalogue: everything the engine knows about a field is one `FieldSpec`.

The plan vocabulary, the prompt glossary, the fan-out bucket lists, the citation paths, the assumption
sentences and `GET /v1/capabilities` are all derived from `CATALOG`. Only types are declared here; the
modules that define the extractors fill `CATALOG`, which cannot be built in this module because they
import it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, Protocol

from ctviz.contract.plan import TimeUnit
from ctviz.ctgov.essie import Expr

if TYPE_CHECKING:
    from ctviz.ctgov.params import Scope
    from ctviz.ctgov.study import Study


@dataclass(frozen=True)
class Evidence:
    """Where a value was read in a study record, and what it was."""

    path: str  # JSON path in the study record, with [i] for list items
    excerpt: str | None  # the exact value at that path; None: absent, and the absence is the evidence


@dataclass(frozen=True)
class Value:
    """One value of a field for one trial: a bucket of a category, a drug, a country, a period."""

    key: str  # stable key of the bucket or entity: "PHASE1_PHASE2", "pembrolizumab", "China"
    label: str  # display label: "Phase 1/Phase 2"
    evidence: tuple[Evidence, ...]
    groups: frozenset[str] = frozenset()  # arm labels; used only by same_arm pairing


@dataclass(frozen=True)
class Bucket:
    """One cell of a closed list of values, with the server expression that selects exactly its trials."""

    key: str
    label: str
    expr: Expr | None  # None when the registry cannot select this bucket on its own


@dataclass(frozen=True)
class Window:
    """The periods of a date axis: every period from `first` to `last`, both inclusive.

    Periods are labels, never dates: `2015`, `2024-Q2`, `2024-06`.
    """

    unit: TimeUnit
    first: str
    last: str


class FieldContext(Protocol):
    """State fitted to one result set before values are extracted, such as the drug normaliser's vocabulary.

    Any object will do; what a field's `extract` needs from it is that field's own business.
    """


FieldContexts = Mapping[str, FieldContext]  # by field key; only fields with a `prepare` step have one


@dataclass(frozen=True)
class BoundDimension:
    """A field as one question uses it: on the axis, as the series, or as the nodes of a network."""

    spec: FieldSpec
    time_unit: TimeUnit | None
    role: Literal["axis", "series", "node"]


@dataclass(frozen=True)
class FieldSpec:
    """Everything the engine knows about one field."""

    key: str  # "phase", "start_date", "drug", ...
    title: str  # axis or legend title
    kind: Literal["category", "entity", "date", "number"]
    pieces: tuple[str, ...]  # API piece names a walk or a bucket sample must project
    extract: Callable[[Study, FieldContext | None, BoundDimension], Sequence[Value]]
    is_exclusive: bool  # every trial has exactly one value, so the values partition the trials
    is_ordinal: bool  # bucket order is meaningful: phase, dates, bins
    buckets: Callable[[BoundDimension, Window | None], Sequence[Bucket]] | None = None  # closed lists only
    bucket_for: Callable[[str], Bucket | None] | None = None  # open but countable: country
    missing: Bucket | None = None  # where trials without a value go; None means excluded and counted
    presence: Expr | None = None  # true when the field has a value; pushed down before a walk
    prepare: Callable[[Sequence[Study]], FieldContext] | None = None  # the fit step
    # Instead of `prepare`, for a field whose values depend on what the question scopes to: state.
    prepare_in_scope: Callable[[Sequence[Study], Scope], FieldContext] | None = None
    hint: str | None = (
        None  # a sentence for the planner's glossary, where the title alone does not say enough
    )
    notes: tuple[str, ...] = ()  # assumption sentences added whenever the field is used


# Keyed by `FieldSpec.key`. A test asserts that the keys equal `DimensionKey` of the plan vocabulary.
CATALOG: Final[dict[str, FieldSpec]] = {}
