"""Words and numbers the question really contains: the facts the grounding rules compare a plan against."""

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final, Literal, Protocol

from ctviz.contract.plan import (
    FAMILY_FIELDS,
    Aggregate,
    EntityKind,
    FilterFamily,
    Phase,
    QueryPlan,
    TrialList,
)
from ctviz.contract.request import QueryRequest

Mode = Literal["model", "structured", "supplied"]

# The request field that names each kind of entity.
ENTITY_FIELDS: Final[dict[str, EntityKind]] = {
    "drug_name": "drug",
    "condition": "condition",
    "sponsor": "sponsor",
    "country": "country",
    "term": "term",
}

_RUN: Final = re.compile(r"[^\W_]+")
_NUMBER_WORDS: Final = {
    word: number
    for number, word in enumerate(
        (
            *("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"),
            *("eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen"),
            *("eighteen", "nineteen", "twenty"),
        ),
        start=1,
    )
}
# "the last five years", "past decade", "since last year", "this year", "two years ago".
_RELATIVE_TIME: Final = re.compile(
    r"\b(?:(?:last|past|previous|recent|this|current|next)\s+(?:[a-z0-9]+\s+)?(?:years?|decades?)"
    r"|years?\s+ago|year\s+to\s+date|recent\s+years)\b"
)
_ROMAN: Final = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10}
_PHASE_MENTION: Final = re.compile(r"\bphase[\s_-]*(\d+|[ivx]+)(?:[a-c])?\b")
_PHASE_NUMERALS: Final[dict[Phase, frozenset[str]]] = {
    "PHASE1": frozenset({"1", "i"}),
    "PHASE2": frozenset({"2", "ii"}),
    "PHASE3": frozenset({"3", "iii"}),
    "PHASE4": frozenset({"4", "iv"}),
}
_PHASE_KEYWORDS: Final[dict[Phase, tuple[str, ...]]] = {
    "EARLY_PHASE1": ("early", "first in human"),
    "PHASE1": ("first in human",),
    "PHASE3": ("late stage", "pivotal"),
    "PHASE4": ("post marketing",),
    "NA": ("not applicable", "n a"),
}
_PLACEHOLDERS: Final = (
    re.compile(r"[\[{<(].*[\]}>)]"),
    re.compile(
        r"(?:this|that|the|a|an|any|some|my|your|these|those)\s+"
        r"(?:drug|medication|treatment|condition|disease|sponsor|company|country|term)s?"
    ),
    re.compile(r"(?:drug|condition|disease|sponsor|country|compound|treatment|company)\s*[a-z0-9]"),
    re.compile(
        r"(?:two|three|four|several|multiple|both|some|any|various)\s+(?:[a-z]+\s+)?"
        r"(?:drugs|conditions|diseases|sponsors|countries|treatments|companies)"
    ),
    re.compile(r"(?:drug|condition|disease|sponsor|country|term)s?|x|y|z|tbd|n/a|unknown|none"),
)


class CountryTable(Protocol):
    """The registry's country names; the plan checks use only these two questions of it."""

    def resolve(self, text: str) -> object | None:
        """Anything but None when `text` names a registry country; the checks never look at what it is."""
        ...

    def suggest(self, text: str) -> Sequence[str]:
        """Close registry names for a text that did not resolve, best first."""
        ...


def tokens(text: str) -> tuple[str, ...]:
    """Maximal runs of letters or digits after NFKC and case folding."""
    return tuple(_RUN.findall(unicodedata.normalize("NFKC", text).casefold()))


def is_grounded(value: str, known: frozenset[str]) -> bool:
    """A value is grounded when it has tokens and every one of them is among the known tokens."""
    found = tokens(value)
    return bool(found) and all(token in known for token in found)


def same_words(first: str, second: str) -> bool:
    return tokens(first) == tokens(second)


def mentions_number(number: int, question_tokens: frozenset[str]) -> bool:
    """The question writes `number` as digits or, from one to twenty, as a word."""
    words = {word for word, value in _NUMBER_WORDS.items() if value == number}
    return str(number) in question_tokens or not words.isdisjoint(question_tokens)


def has_relative_time(question: str) -> bool:
    return _RELATIVE_TIME.search(unicodedata.normalize("NFKC", question).casefold()) is not None


def unknown_phase_mentions(question: str) -> list[str]:
    """The phase spellings in the question whose number is outside 1 to 4."""
    found = []
    for match in _PHASE_MENTION.finditer(unicodedata.normalize("NFKC", question).casefold()):
        word = match[1]
        number = int(word) if word.isdigit() else _ROMAN.get(word)
        if number is not None and not 1 <= number <= 4:
            found.append(match[0])
    return found


def phase_is_grounded(phase: Phase, phrase: str) -> bool:
    """The evidence phrase holds the phase's numeral (arabic or roman) or one of its keywords."""
    words = tokens(phrase)
    if not _PHASE_NUMERALS.get(phase, frozenset()).isdisjoint(words):
        return True
    padded = f" {' '.join(words)} "
    return any(f" {keyword} " in padded for keyword in _PHASE_KEYWORDS.get(phase, ()))


def is_placeholder(value: str) -> bool:
    text = " ".join(unicodedata.normalize("NFKC", value).casefold().split())
    return any(pattern.fullmatch(text) for pattern in _PLACEHOLDERS)


def request_words(request: QueryRequest | None) -> list[str]:
    """Every word a structured field states, as text: entity values, compared values, years and top_n."""
    if request is None:
        return []
    words = [value for name in ENTITY_FIELDS for value in getattr(request, name) or []]
    if request.exclude is not None:
        words.extend(value for name in ENTITY_FIELDS for value in getattr(request.exclude, name) or [])
    if request.compare is not None:
        words.extend(request.compare.values)
    numbers = (request.start_year, request.end_year, request.top_n)
    return words + [str(number) for number in numbers if number is not None]


@dataclass(frozen=True)
class Facts:
    """What the question and the structured fields state, and the context the rules need."""

    mode: Mode
    request: QueryRequest | None
    today: date
    countries: CountryTable | None
    question_tokens: frozenset[str]
    known: frozenset[str]  # question tokens plus the tokens of every structured value

    @classmethod
    def of(
        cls, mode: Mode, request: QueryRequest | None, today: date, countries: CountryTable | None
    ) -> "Facts":
        question = request.query if request is not None else ""
        question_tokens = frozenset(tokens(question))
        return cls(
            mode,
            request,
            today,
            countries,
            question_tokens,
            question_tokens | _all_tokens(request_words(request)),
        )

    @property
    def question(self) -> str:
        return self.request.query if self.request is not None else ""

    @property
    def previous(self) -> QueryPlan | None:
        """The plan of the previous turn, when the request is a follow-up."""
        previous = self.request.previous if self.request is not None else None
        return previous.plan if previous is not None else None

    def carries_entity(self, kind: str, value: str) -> bool:
        """The previous plan names this entity (same kind and words): a value carried over, not invented."""
        previous = self.previous
        words = tokens(value)
        return previous is not None and any(
            entity.kind == kind and tokens(entity.value) == words for entity in previous.entities
        )

    def carries_year(self, attribute: str, year: int) -> bool:
        previous = self.previous
        return previous is not None and getattr(previous.filters, attribute) == year

    def carries_filter(self, family: FilterFamily, values: Sequence[str]) -> bool:
        """The previous plan restricts this family to exactly these values."""
        previous = self.previous
        return previous is not None and set(getattr(previous.filters, family)) == set(values)

    def carries_number(self, attribute: str, number: int) -> bool:
        """The previous plan has this `top_n` or `limit`."""
        previous = self.previous
        analysis = previous.analysis if previous is not None else None
        return isinstance(analysis, Aggregate | TrialList) and getattr(analysis, attribute, None) == number

    def carries_phrase(self, phrase: str) -> bool:
        """The previous plan already lists this unapplied wording."""
        previous = self.previous
        return previous is not None and any(same_words(phrase, old) for old in previous.unapplied)

    @property
    def carried_numbers(self) -> frozenset[str]:
        """Digit runs the previous plan holds (years, a count): an interpretation may repeat them."""
        previous = self.previous
        if previous is None:
            return frozenset()
        numbers: list[int | None] = [previous.filters.year_from, previous.filters.year_to]
        if isinstance(previous.analysis, Aggregate | TrialList):
            numbers.append(
                getattr(previous.analysis, "top_n", None) or getattr(previous.analysis, "limit", None)
            )
        return frozenset(str(number) for number in numbers if number is not None)

    @property
    def request_families(self) -> frozenset[str]:
        """The filter families a structured field states; their values are the client's, not the model's."""
        request = self.request
        if request is None:
            return frozenset()
        fields = {family: getattr(request, name) for family, name in FAMILY_FIELDS.items()}
        fields["exclude_statuses"] = request.exclude.status if request.exclude is not None else None
        return frozenset(family for family, values in fields.items() if values)


def _all_tokens(texts: Iterable[str]) -> frozenset[str]:
    return frozenset(token for text in texts for token in tokens(text))
