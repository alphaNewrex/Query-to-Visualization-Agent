"""Citations: pointers from every number into the trial records it was computed from (section 4.13).

The book collects the trials that were cited, so `references` holds exactly those and the title and URL
of a trial appear once however many data rows cite it.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ctviz.catalog.fields import Evidence
from ctviz.contract.response import Citation, ScopeEvidence, TrialReference
from ctviz.engine.frame import Cell

STUDY_URL = "https://clinicaltrials.gov/study/{nct_id}"
RECORD_URL = "https://clinicaltrials.gov/api/v2/studies/{nct_id}"


@dataclass(frozen=True)
class Cited:
    """The three reserved keys of one data row."""

    citations: list[Citation]
    citation_count: int
    source_url: str | None


class CitationBook:
    """Turns cells and trials into citations, capped per datum, and remembers who was cited."""

    def __init__(
        self,
        *,
        max_per_datum: int,
        titles: Mapping[str, str],
        scope_evidence: Mapping[str, Sequence[ScopeEvidence]] | None = None,
    ) -> None:
        self._max = max_per_datum
        self._titles = titles
        self._scope_evidence = scope_evidence or {}
        self._cited: dict[str, None] = {}  # insertion-ordered set

    def for_cell(self, cell: Cell) -> Cited:
        """The cell's best trials, up to the cap; the count is every trial behind the cell."""
        citations: list[Citation] = []
        seen: set[str] = set()
        for trial in cell.sample:
            if len(seen) == self._max:
                break
            if trial.nct_id not in seen:
                seen.add(trial.nct_id)
                citations.extend(self._cite(trial.nct_id, trial.evidence))
        return Cited(citations, cell.trials, cell.source_url)

    def for_trial(self, nct_id: str, evidence: Sequence[Evidence]) -> Cited:
        """A scatter point or a table row cites itself, and links to its own record."""
        citations = self._cite(nct_id, evidence) if self._max > 0 else []
        return Cited(citations, 1, RECORD_URL.format(nct_id=nct_id))

    def references(self) -> dict[str, TrialReference]:
        return {
            nct_id: TrialReference(
                title=self._titles.get(nct_id, ""),
                url=STUDY_URL.format(nct_id=nct_id),
                scope_evidence=list(self._scope_evidence.get(nct_id, ())),
            )
            for nct_id in self._cited
        }

    @property
    def max_per_datum(self) -> int:
        return self._max

    def _cite(self, nct_id: str, evidence: Sequence[Evidence]) -> list[Citation]:
        if (
            evidence
        ):  # a trial with nothing to show is not cited, so `references` stays exactly the cited trials
            self._cited[nct_id] = None
        return [Citation(nct_id=nct_id, field=item.path, excerpt=item.excerpt) for item in evidence]
