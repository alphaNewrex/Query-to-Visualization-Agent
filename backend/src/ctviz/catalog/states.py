"""The state dimension: the state, province or region that a trial's sites are in.

State names are read from the records, never listed here. States exist in many countries, so which sites
are grouped depends on the question: when it names a country (an entity of kind country, as a filter or as
a compared side), only the sites in that country count; when it names none, every site counts and a state
name shared by two countries is one group. `StateScope` carries that choice from the scope to the extractor.

Names are free text: one state is written "Málaga" and "Malaga", "A Coruña" and "A Coruna". Names that
differ only in case, accents, punctuation or white space are one state, keyed by the folded name and shown
under the spelling the records use most.
"""

import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from ctviz.catalog.countries import load_country_table
from ctviz.catalog.fields import Bucket
from ctviz.catalog.spellings import commonest_spellings
from ctviz.ctgov import essie
from ctviz.ctgov.params import Scope
from ctviz.ctgov.study import Study


def fold_state(raw: str) -> str:
    """The key of a state name: without accents, case or punctuation, white space collapsed."""
    plain = "".join(
        char for char in unicodedata.normalize("NFKD", raw) if not unicodedata.combining(char)
    ).casefold()
    return " ".join("".join(char if char.isalnum() else " " for char in plain).split())


@dataclass(frozen=True)
class StateScope:
    """The countries whose sites are grouped by state (empty: every country), and how states are shown."""

    countries: tuple[str, ...]
    # The commonest raw spelling of each folded state name, fitted to the trials read.
    labels: Mapping[str, str] = field(default_factory=dict, compare=False)

    @classmethod
    def of(cls, scope: Scope) -> "StateScope":
        """The registry names of the countries a scope is limited to, in the order the question gave them."""
        table = load_country_table()
        names = (table.resolve(term.text) or term.text for term in scope.terms if term.kind == "country")
        return cls(tuple(dict.fromkeys(names)))

    def includes(self, country: str | None) -> bool:
        return not self.countries or (country is not None and country.strip() in self.countries)


def fit(studies: Sequence[Study], scope: Scope) -> StateScope:
    """The scope's countries, and the spelling each state is shown under in these trials."""
    chosen = StateScope.of(scope)
    pairs = (
        (fold_state(location.state), location.state.strip())
        for study in studies
        for location in study.locations
        if location.state is not None and chosen.includes(location.country)
    )
    return StateScope(chosen.countries, commonest_spellings((key, raw) for key, raw in pairs if key))


def bucket_for(key: str) -> Bucket | None:
    """The registry search for a folded state name; the registry reads it without case or accents too."""
    try:
        return Bucket(key, key, essie.area("LocationState", key))
    except ValueError:
        return None


def bucket_in(key: str, scope: Scope) -> Bucket | None:
    """The search for the trials with a site in a state, at a site of the countries the scope names.

    A country and a state must hold at one site: a trial with a site in Spain and another in a state
    that Spain's regions share a name with elsewhere is not in the bar of that state.
    """
    bucket = bucket_for(key)
    if bucket is None or bucket.expr is None:
        return None
    countries = StateScope.of(scope).countries
    if not countries:
        return bucket
    in_countries = essie.or_(*(essie.area("LocationCountry", name) for name in countries))
    return Bucket(key, key, essie.at_one_site(essie.and_(in_countries, bucket.expr)))
