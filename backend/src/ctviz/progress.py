"""Progress of one request, as the pipeline reports it. The pipeline knows nothing of how it is delivered."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

Step = Literal["plan", "check", "resolve", "strategy", "execute", "build"]
Status = Literal["started", "done"]


@dataclass(frozen=True)
class StageEvent:
    """A stage began or ended. `summary` is written by code from the data the stage produced."""

    step: Step
    status: Status
    summary: str
    detail: dict[str, object] = field(default_factory=dict)


# Called synchronously from the pipeline's task, so it must not block; a failure in it is the caller's.
Progress = Callable[[StageEvent], None]
