"""The search part of a ClinicalTrials.gov request, and the one canonical URL it becomes.

A `Scope` is the set of trials a question is about. `Scope.params()` writes it in one fixed form, so the
same scope always gives the same URL. That URL is the cache key of the client and the `source_url`
that a response cites: anyone who opens it gets the same trials.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Final, Self
from urllib.parse import quote

from ctviz.contract.plan import EntityKind
from ctviz.contract.response import MatchDefinition
from ctviz.ctgov import essie
from ctviz.ctgov.essie import Expr

# The only parameters a search term may be written to, so that no text can name a parameter.
QUERY_PARAMETERS: Final = frozenset({"query.cond", "query.intr", "query.lead", "query.term"})
_STATUS_PARAMETER: Final = "filter.overallStatus"
# Enum filters that become one `AREA[...]` group, by dimension key. The catalogue knows the same
# pieces, but it sits above this package and cannot be imported here.
_FILTER_AREAS: Final = {
    "phase": "Phase",
    "study_type": "StudyType",
    "sponsor_class": "LeadSponsorClass",
    "intervention_type": "InterventionType",
}
# Commas and colons are legal in a query string and keep `fields` and `sort` readable. Everything else
# that is not plain is encoded, brackets included: `curl` reads a raw `[` as the start of a range.
_URL_SAFE: Final = ",:"


@dataclass(frozen=True)
class Params:
    """What the trials of one search must match: plain parameters and the one `filter.advanced`."""

    # `query.*` and `filter.overallStatus`; sorted by name, searches first.
    texts: tuple[tuple[str, str], ...] = ()
    advanced: Expr | None = None

    def __post_init__(self) -> None:
        # Frozen, so the sort goes through object.__setattr__. Equal searches must compare equal.
        object.__setattr__(self, "texts", tuple(sorted(self.texts, key=_searches_first)))

    def pairs(self) -> tuple[tuple[str, str], ...]:
        """Every parameter as (name, value), in the canonical order."""
        if self.advanced is None:
            return self.texts
        return (*self.texts, ("filter.advanced", self.advanced))

    def narrowed_by(self, expr: Expr) -> Self:
        """The same search, limited to the trials that `expr` also selects."""
        advanced = expr if self.advanced is None else essie.and_(self.advanced, expr)
        return replace(self, advanced=advanced)


@dataclass(frozen=True)
class DateRange:
    """Trials whose date in `piece` lies between two days, both inclusive; None leaves that end open."""

    piece: str  # the API piece: StartDate, PrimaryCompletionDate, CompletionDate, StudyFirstPostDate
    first_day: str | None  # YYYY-MM-DD
    last_day: str | None

    def expr(self) -> Expr:
        return essie.range_(self.piece, self.first_day, self.last_day)


@dataclass(frozen=True)
class BoundTerm:
    """One entity of the question, bound to the way the registry searches for it."""

    kind: EntityKind
    text: str  # as the user wrote it
    term: str  # after essie.literal()
    parameter: str | None  # the `query.*` parameter that carries `term`
    expr: Expr | None  # set instead when the term compiles to `filter.advanced`: a country, a drug by name
    definition: MatchDefinition
    note: str  # the assumption sentence for meta.assumptions

    def __post_init__(self) -> None:
        if (self.parameter is None) == (self.expr is None):
            raise ValueError("A term compiles either to a query parameter or to an expression.")
        if self.parameter is not None and self.parameter not in QUERY_PARAMETERS:
            raise ValueError(f"Not a search parameter: {self.parameter!r}.")


@dataclass(frozen=True)
class Scope:
    """One set of trials. With several scopes, each is one series of the answer."""

    id: str  # "s0", "s1", ...
    label: str | None  # the compared value; None for a single scope
    terms: tuple[BoundTerm, ...]
    enum_filters: Mapping[str, tuple[str, ...]]  # {"phase": ("PHASE3",), "overall_status": ("RECRUITING",)}
    date_range: DateRange | None
    extra: tuple[Expr, ...] = ()  # a presence push-down added by the strategy

    def params(self) -> Params:
        """The search that selects exactly this scope, in canonical form.

        Terms of one parameter are joined with AND, a status filter is a `|` list, every other filter
        is one group, and all expressions share the one `filter.advanced`.
        """
        by_parameter: dict[str, list[str]] = {}
        clauses: list[Expr] = []
        for term in self.terms:
            if term.parameter is not None:
                by_parameter.setdefault(term.parameter, []).append(term.term)
            elif term.expr is not None:
                clauses.append(term.expr)
        texts = [(name, essie.match_all(terms)) for name, terms in by_parameter.items()]

        for key in sorted(self.enum_filters):
            values = self.enum_filters[key]
            if not values:
                continue
            if key == "overall_status":
                texts.append((_STATUS_PARAMETER, "|".join(values)))
            elif key in _FILTER_AREAS:
                clauses.append(essie.any_of(_FILTER_AREAS[key], values))
            else:
                raise ValueError(f"No filter is defined for {key!r}.")
        if self.date_range is not None:
            clauses.append(self.date_range.expr())
        clauses.extend(self.extra)
        return Params(tuple(texts), essie.and_(*clauses) if clauses else None)


def canonical_url(
    base_url: str,
    params: Params,
    *,
    count_total: bool = False,
    page_size: int | None = None,
    fields: Sequence[str] = (),
    sort: str | None = None,
    page_token: str | None = None,
) -> str:
    """The `/studies` URL of a search, the same text for the same request however it was assembled.

    Parameters come in a fixed order (the search, then `countTotal`, `pageSize`, `fields`, `sort`,
    `pageToken`). A projection always lists `NCTId` first and the other pieces alphabetically, because
    the registry ignores their order and a record without its NCT ID is of no use.
    """
    pairs = list(params.pairs())
    if count_total:
        pairs.append(("countTotal", "true"))
    if page_size is not None:
        pairs.append(("pageSize", str(page_size)))
    if fields:
        pairs.append(("fields", ",".join(["NCTId", *sorted(set(fields) - {"NCTId"})])))
    if sort is not None:
        pairs.append(("sort", sort))
    if page_token is not None:
        pairs.append(("pageToken", page_token))
    query = "&".join(f"{name}={quote(value, safe=_URL_SAFE)}" for name, value in pairs)
    return f"{base_url.rstrip('/')}/studies?{query}"


def _searches_first(pair: tuple[str, str]) -> tuple[bool, str]:
    name = pair[0]
    return (not name.startswith("query."), name)
