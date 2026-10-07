"""The state dimension: the state, province or region that a trial's sites are in.

State names are read from the records, never listed here. States exist in many countries, so which sites
are grouped depends on the question: when it names a country (an entity of kind country, as a filter or as
a compared side), only the sites in that country count; when it names none, every site counts and a state
name shared by two countries is one group. `StateScope` carries that choice from the scope to the extractor.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from ctviz.catalog.countries import load_country_table
from ctviz.ctgov.params import Scope
from ctviz.ctgov.study import Study


@dataclass(frozen=True)
class StateScope:
    """The countries whose sites are grouped by state; empty means every country."""

    countries: tuple[str, ...]

    @classmethod
    def of(cls, scope: Scope) -> "StateScope":
        """The registry names of the countries a scope is limited to, in the order the question gave them."""
        table = load_country_table()
        names = (table.resolve(term.text) or term.text for term in scope.terms if term.kind == "country")
        return cls(tuple(dict.fromkeys(names)))

    def includes(self, country: str | None) -> bool:
        return not self.countries or (country is not None and country.strip() in self.countries)


def fit(_studies: Sequence[Study], scope: Scope) -> StateScope:
    return StateScope.of(scope)
