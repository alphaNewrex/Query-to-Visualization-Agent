"""The one intermediate table of the engine: both executors fill a `Frame`, and every builder reads one.

A walk feeds records to the group-by; a fan-out fills the same table from count calls, one cell per
bucket. Only the types are declared here, so one builder per chart type serves both executors.
"""

from dataclasses import dataclass, field

from ctviz.catalog.fields import BoundDimension, Evidence
from ctviz.contract.response import Note, StrategyName
from ctviz.ctgov.essie import Expr
from ctviz.ctgov.params import Scope


@dataclass(frozen=True)
class TrialEvidence:
    """One trial's evidence for one cell: what to cite, and how it ranks among the cell's trials."""

    nct_id: str
    evidence: tuple[Evidence, ...]
    # The citation rule: a trial in which a scope term occurs in an intervention name, another name, a
    # condition or the lead sponsor name, then the most recently first-posted, then NCT ID descending.
    # Written as (is_named, first_posted, nct_id), so a larger rank is a better citation.
    rank: tuple[bool, str, str]


@dataclass
class Cell:
    """The trials of one combination of dimension values."""

    key: tuple[str, ...]  # one key per dimension
    labels: tuple[str, ...] = ()
    trials: int = 0  # distinct trials in the cell
    sample: list[TrialEvidence] = field(default_factory=list)  # at most the frame's sample_size, best first
    expr: Expr | None = None  # Essie selecting exactly these trials, when every bucket has one
    source_url: str | None = None  # the URL that was called (fan-out) or that can be composed (walk)
    values: list[float] = field(default_factory=list)  # a measure: the value of each of the `trials`

    def add(self, trial: TrialEvidence, sample_size: int, value: float | None = None) -> None:
        """Count one trial and offer it to the sample, which keeps the best-ranked `sample_size`."""
        self.trials += 1
        if value is not None:
            self.values.append(value)
        if sample_size == 0 or (len(self.sample) == sample_size and trial.rank <= self.sample[-1].rank):
            return
        self.sample.append(trial)
        self.sample.sort(key=lambda kept: kept.rank, reverse=True)
        del self.sample[sample_size:]


@dataclass
class Exclusion:
    """Trials left out for one reason, and the sentence that says so."""

    message: str
    count: int = 0


@dataclass
class Frame:
    """The cells of one scope, with the counters that make its counts reconcile."""

    scope: Scope
    dims: tuple[BoundDimension, ...]
    sample_size: int  # how many trials each cell may keep as citations
    cells: dict[tuple[str, ...], Cell] = field(default_factory=dict)
    marginals: tuple[dict[str, Cell], ...] = ()  # one table per dimension: node sizes, share denominators
    matched: int = 0  # totalCount of the scope
    seen: int = 0  # trials actually read; differs from matched when the registry changed during a walk
    analyzed: int = 0
    excluded: dict[str, Exclusion] = field(default_factory=dict)  # by reason
    strategy: StrategyName = "walk"
    warnings: list[Note] = field(default_factory=list)  # what the executor found out about this scope

    def __post_init__(self) -> None:
        if not self.marginals:
            self.marginals = tuple({} for _ in self.dims)

    def cell(self, key: tuple[str, ...], labels: tuple[str, ...]) -> Cell:
        """The cell of a combination of values, created empty on first use."""
        if key not in self.cells:
            self.cells[key] = Cell(key=key, labels=labels)
        return self.cells[key]

    def exclude(self, reason: str, message: str, count: int = 1) -> None:
        """Leave `count` trials out of the analysis, for a reason that the response states."""
        self.excluded.setdefault(reason, Exclusion(message)).count += count
