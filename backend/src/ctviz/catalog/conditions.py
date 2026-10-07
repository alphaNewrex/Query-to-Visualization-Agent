"""Condition names: free text, folded to one key each and shown under the spelling used most often.

Nothing more is done to a condition. Spellings of one disease that differ in more than case and white
space stay separate, which the `free_text_categories` warning says.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Self

from ctviz.catalog.spellings import commonest_spellings
from ctviz.ctgov.study import Study


def fold(raw: str) -> str:
    """Case-folded, with white space collapsed."""
    return " ".join(raw.casefold().split())


@dataclass(frozen=True)
class ConditionLabels:
    """Fitted to one result set: the commonest raw spelling of each folded condition."""

    labels: Mapping[str, str]

    @classmethod
    def fit(cls, studies: Sequence[Study]) -> Self:
        pairs = ((fold(raw), raw) for study in studies for raw in study.conditions)
        return cls(commonest_spellings((key, raw) for key, raw in pairs if key))
