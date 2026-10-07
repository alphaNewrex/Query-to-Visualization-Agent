"""Essie, the registry's query language: the only module that writes `filter.advanced` text.

The registry reads the text of every search parameter as an Essie expression, so a word such as `ALL`
or `NOT`, or an injected `AREA[Phase]PHASE4`, changes what a search means. User words therefore pass
through `literal()`, and expressions are built only by the functions below, from enum tokens that code
supplies and never from text.
"""

import re
import unicodedata
from collections.abc import Sequence
from typing import Final, NewType

Expr = NewType("Expr", str)

# Upper-case, these are operators; lower-case, the same words are plain terms (measured).
_OPERATOR_WORDS: Final = frozenset(
    {
        "ALL",
        "AND",
        "OR",
        "NOT",
        "AREA",
        "SEARCH",
        "RANGE",
        "MISSING",
        "MIN",
        "MAX",
        "COVERAGE",
        "EXPANSION",
        "TILT",
        "DISTANCE",
    }
)
# In a bare term, each of these would open a group, a bracket, a phrase or an escape, so each becomes a
# space. Inside a phrase only the last two can do harm.
_STRUCTURAL: Final = str.maketrans(dict.fromkeys('[]()"\\', " "))
_PHRASE_BREAKERS: Final = str.maketrans(dict.fromkeys('"\\', " "))

_ENUM_TOKEN: Final = re.compile(r"[A-Z][A-Z0-9_]*|true|false")
_PIECE: Final = re.compile(r"[A-Za-z][A-Za-z0-9:]*")
_ISO_DAY: Final = re.compile(r"\d{4}-\d{2}-\d{2}")
_LETTERS_AND_DIGITS: Final = re.compile(r"[^\W_]+")


def literal(text: str) -> str:
    """User or model words as a search term that the registry reads as words and nothing else.

    Operator words are escaped with a backslash, which is the escape the registry documents, also when
    punctuation touches them (`AML,ALL` becomes `AML,\\ALL`). Apostrophes, commas, ampersands, slashes
    and hyphens pass unchanged: they are part of real names such as `Merck KGaA, Darmstadt, Germany`.

    Raises ValueError when no letter or digit is left to search for.
    """
    cleaned = " ".join(_normalised(text).translate(_STRUCTURAL).split())
    if not any(char.isalnum() for char in cleaned):
        raise ValueError("The text has no letter or digit to search for.")
    return _LETTERS_AND_DIGITS.sub(_escape_operator, cleaned)


def match_all(terms: Sequence[str]) -> str:
    """Search terms that must all match, for one `query.*` parameter: `(a) AND (b)`, or one term as it is.

    Measured: `query.intr=(pembrolizumab) AND (nivolumab)` returns 296, the trials in both sets.
    """
    if not terms:
        raise ValueError("At least one term is needed.")
    if len(terms) == 1:
        return terms[0]
    return " AND ".join(f"({term})" for term in terms)


def area(piece: str, value: str) -> Expr:
    """`AREA[piece]value`: an enum token as it is, any other text as a quoted phrase.

    A token that reads as an operator is quoted too: `AREA[Sex]ALL` matches every study, because `ALL`
    is the operator, and `AREA[Sex]"ALL"` is the 521,619 studies that accept all sexes.
    """
    return Expr(f"AREA[{_checked_piece(piece)}]{_term(value)}")


def any_of(piece: str, values: Sequence[str]) -> Expr:
    """`AREA[Phase](PHASE2 OR PHASE3)`: the area holds at least one of the enum tokens."""
    return _in_area(piece, _enum_tokens(values), joiner=" OR ")


def all_of(piece: str, values: Sequence[str], *, but_not: Sequence[str] = ()) -> Expr:
    """`AREA[Phase](PHASE1 AND NOT PHASE2)`: the area holds every token of `values` and none of `but_not`.

    A trial lists one or two phases, and `AREA[Phase]PHASE2` means "lists Phase 2", so the exclusive
    phase buckets are written with this.
    """
    excluded = [f"NOT {token}" for token in _enum_tokens(but_not, allow_empty=True)]
    return _in_area(piece, [*_enum_tokens(values), *excluded], joiner=" AND ")


def range_(piece: str, lo: str | int | None, hi: str | int | None) -> Expr:
    """`AREA[StartDate]RANGE[2015-01-01,MAX]`: both ends inclusive, an open end for None.

    An end is a date as `YYYY-MM-DD` or a whole number, and nothing else, so no text reaches a range.
    """
    if lo is None and hi is None:
        raise ValueError("A range needs at least one end.")
    return Expr(f"AREA[{_checked_piece(piece)}]RANGE[{_bound(lo, 'MIN')},{_bound(hi, 'MAX')}]")


def missing(piece: str) -> Expr:
    """`AREA[Phase]MISSING`: the studies with no value in the area."""
    return Expr(f"AREA[{_checked_piece(piece)}]MISSING")


def and_(*parts: Expr) -> Expr:
    """The expressions joined with AND, each in parentheses so that none can change another's meaning."""
    if not parts:
        raise ValueError("and_ needs at least one expression.")
    if len(parts) == 1:
        return parts[0]
    return Expr(" AND ".join(f"({part})" for part in parts))


def not_(part: Expr) -> Expr:
    """The studies that `part` does not select."""
    return Expr(f"NOT ({part})")


def _normalised(text: str) -> str:
    """NFKC with the control characters dropped; white space stays for the caller to collapse."""
    return "".join(
        char
        for char in unicodedata.normalize("NFKC", text)
        if char.isspace() or not unicodedata.category(char).startswith("C")
    )


def _escape_operator(word: re.Match[str]) -> str:
    return f"\\{word.group()}" if word.group() in _OPERATOR_WORDS else word.group()


def _checked_piece(piece: str) -> str:
    if not _PIECE.fullmatch(piece):
        raise ValueError(f"Not an area name: {piece!r}.")
    return piece


def _term(value: str) -> str:
    """An enum token as it is, quoted when it reads as an operator; any other text as a phrase."""
    if _ENUM_TOKEN.fullmatch(value) and value not in _OPERATOR_WORDS:
        return value
    return _phrase(value)


def _phrase(text: str) -> str:
    """Free text in quotes, where an operator word is only a word."""
    cleaned = " ".join(_normalised(text).translate(_PHRASE_BREAKERS).split())
    if not cleaned:
        raise ValueError("The text is empty.")
    return f'"{cleaned}"'


def _enum_tokens(values: Sequence[str], *, allow_empty: bool = False) -> list[str]:
    if not values and not allow_empty:
        raise ValueError("At least one value is needed.")
    for value in values:
        if not _ENUM_TOKEN.fullmatch(value):
            raise ValueError(f"Not an enum token: {value!r}.")
    return [_term(value) for value in values]


def _in_area(piece: str, terms: Sequence[str], *, joiner: str) -> Expr:
    """`AREA[piece](a OR b)`, without the parentheses when there is a single term."""
    checked = _checked_piece(piece)
    if len(terms) == 1:
        return Expr(f"AREA[{checked}]{terms[0]}")
    return Expr(f"AREA[{checked}]({joiner.join(terms)})")


def _bound(value: str | int | None, open_end: str) -> str:
    if value is None:
        return open_end
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return str(value)
    if isinstance(value, str) and _ISO_DAY.fullmatch(value):
        return value
    raise ValueError(f"Not a date (YYYY-MM-DD) or a non-negative whole number: {value!r}.")
