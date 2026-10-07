"""The record of one request: what was asked of the registry and what it returned.

It is the one object the stages share. The client writes to it through `RequestLog`, and the invariant
check reads it through `Provenance`, so a citation can be proved against the records actually received.
"""

import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field

from ctviz.contract.response import TraceStep, UpstreamRequest
from ctviz.ctgov.study import Study


@dataclass
class RequestContext:
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    requests: list[UpstreamRequest] = field(default_factory=list)
    trace: list[TraceStep] = field(default_factory=list)
    _studies: dict[str, Study] = field(default_factory=dict)

    def log_request(self, entry: UpstreamRequest) -> None:
        self.requests.append(entry)

    def note_studies(self, studies: Iterable[Study]) -> None:
        self._studies.update((study.nct_id, study) for study in studies)

    @property
    def titles(self) -> dict[str, str]:
        """The brief title of every trial returned, for the references."""
        return {nct_id: study.brief_title or "" for nct_id, study in self._studies.items()}

    def was_returned(self, nct_id: str) -> bool:
        return nct_id in self._studies

    def value_at(self, nct_id: str, path: str) -> str | None:
        study = self._studies.get(nct_id)
        found = None if study is None else study.value_at(path)
        if found is None or isinstance(found, str):
            return found
        return json.dumps(found)  # numbers and booleans in JSON notation, as a citation writes them

    def total_count(self, url: str) -> int | None:
        counts = [entry.total_count for entry in self.requests if entry.url == url]
        return counts[0] if counts and counts[0] is not None else None

    def add_step(
        self,
        type_: str,
        summary: str,
        duration_ms: int,
        first_request: int,
        detail: dict[str, object] | None = None,
    ) -> None:
        """Record a stage; its requests are those logged since index `first_request`."""
        self.trace.append(
            TraceStep.model_validate(
                {
                    "index": len(self.trace),
                    "type": type_,
                    "summary": summary,
                    "duration_ms": duration_ms,
                    "request_indexes": list(range(first_request, len(self.requests))),
                    "detail": detail or {},
                }
            )
        )
