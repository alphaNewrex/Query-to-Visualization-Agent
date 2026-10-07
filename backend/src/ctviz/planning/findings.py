"""What the plan rules found: fixes made, warnings raised and problems that need the model."""

from dataclasses import dataclass, field
from typing import Literal

from ctviz.contract.plan import PlanIssue
from ctviz.contract.response import Adjustment, Note

Action = Literal["dropped", "replaced", "clamped", "defaulted", "repaired", "kept"]


@dataclass
class Findings:
    """Collects the results of one `check_plan` run, in the order the rules produced them."""

    adjustments: list[Adjustment] = field(default_factory=list)
    warnings: list[Note] = field(default_factory=list)
    blocking: list[PlanIssue] = field(default_factory=list)

    def adjust(self, code: str, path: str, message: str, action: Action) -> None:
        self.adjustments.append(Adjustment(code=code, path=path, message=message, action=action))

    def warn(self, code: str, message: str) -> None:
        self.warnings.append(Note(code=code, message=message))

    def block(self, code: str, path: str, message: str, allowed: tuple[str, ...] = ()) -> None:
        self.blocking.append(PlanIssue(code=code, path=path, message=message, allowed=allowed))
