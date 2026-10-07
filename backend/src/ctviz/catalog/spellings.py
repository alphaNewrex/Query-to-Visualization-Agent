"""Free text merged under one key is shown under the spelling people wrote most often."""

from collections import Counter
from collections.abc import Iterable


def commonest_spellings(pairs: Iterable[tuple[str, str]]) -> dict[str, str]:
    """For each key, the raw spelling it was seen with most often.

    A tie goes to the alphabetically first spelling, so a label does not depend on the order in which
    the registry sent the trials.
    """
    seen: dict[str, Counter[str]] = {}
    for key, raw in pairs:
        seen.setdefault(key, Counter())[raw] += 1
    return {key: min(counts, key=lambda raw: (-counts[raw], raw)) for key, counts in seen.items()}
