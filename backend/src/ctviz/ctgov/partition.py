"""Splitting a scope into disjoint ranges of first-posted date that together hold every trial of it.

A paged walk is sequential: each page's token comes from the page before. Ranges of `StudyFirstPostDate`
give independent token chains. The date is never missing, so the ranges cover every trial, and the
registry counts a range exactly, so the boundaries are chosen from counts and not from a guess: a range
holding more than `leaf_max` trials is cut at its middle day, and the count of the right half is the
range's count minus the left half's, which costs one count call per cut.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final

import anyio

from ctviz.ctgov import essie
from ctviz.ctgov.params import Params

PIECE: Final = "StudyFirstPostDate"
# A walk of up to this many pages is one token chain; a longer one is split into ranges of days.
SEQUENTIAL_PAGES: Final = 2

Counter = Callable[[Params], Awaitable[int]]


@dataclass(frozen=True)
class Partition:
    """One range of days with the trials the registry counted in it."""

    params: Params
    expected: int


def as_day(text: str) -> date:
    """A registry date: `2026-03-05`, or `2026-03` which stands for the first of the month."""
    return date.fromisoformat(text if len(text) == 10 else f"{text}-01")


async def partition(
    params: Params, total: int, first: date, last: date, *, leaf_max: int, count: Counter
) -> list[Partition]:
    """Ranges of days from `first` to `last` that cover the trials of `params`, none holding over `leaf_max`.

    The first range is open at its start and the last at its end, so a trial outside `first` and `last`
    is still in one of them. A range that is a single day is not cut further, however many trials it holds.
    Ranges that hold nothing are left out.
    """
    if leaf_max < 1:
        raise ValueError("A range holds at least one trial.")
    found: list[tuple[date, Partition]] = []

    async def cut(lo: date, hi: date, size: int, is_first: bool, is_last: bool) -> None:
        if size <= 0:
            return
        if size <= leaf_max or lo >= hi:
            if is_first and is_last:
                # One range with both ends open is the whole search: every trial shares one day, or fits.
                found.append((lo, Partition(params, size)))
                return
            expr = essie.range_(
                PIECE, None if is_first else lo.isoformat(), None if is_last else hi.isoformat()
            )
            found.append((lo, Partition(params.narrowed_by(expr), size)))
            return
        middle = lo + timedelta(days=(hi - lo).days // 2)
        left = await count(
            params.narrowed_by(essie.range_(PIECE, None if is_first else lo.isoformat(), middle.isoformat()))
        )
        try:
            async with anyio.create_task_group() as group:
                group.start_soon(cut, lo, middle, left, is_first, False)
                group.start_soon(cut, middle + timedelta(days=1), hi, size - left, False, is_last)
        except ExceptionGroup as failures:
            raise _first_failure(failures) from failures

    await cut(first, last, total, True, True)
    return [part for _, part in sorted(found, key=lambda item: item[0])]


def _first_failure(failures: ExceptionGroup[Exception]) -> Exception:
    first = failures.exceptions[0]
    return _first_failure(first) if isinstance(first, ExceptionGroup) else first
