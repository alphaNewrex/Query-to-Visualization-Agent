"""Entity resolution: exact counts, statuses, warnings, scopes and the no-data explanations."""

from dataclasses import dataclass, field

import pytest

from ctviz.contract.plan import Aggregate, Entity, QueryPlan
from ctviz.contract.request import QueryRequest, RequestOptions
from ctviz.contract.response import Outcome
from ctviz.ctgov import essie
from ctviz.ctgov.params import DateRange, Params
from ctviz.engine.lower import Resolved
from ctviz.engine.resolve import EntityResolver, bind_term, resolve_entities

from .engine_support import PUBLIC_PLAN, FakeClient, FakeContext

pytestmark = pytest.mark.anyio


class Countries:
    def resolve(self, text: str) -> str | None:
        return {"turkey": "Turkey (Türkiye)", "usa": "United States"}.get(text.casefold())


def search(parameter: str, text: str) -> Params:
    return Params(((parameter, text),))


def strict(text: str) -> Params:
    return Params(advanced=essie.area("InterventionName", text))


@dataclass(frozen=True)
class Planned:
    plan: QueryPlan
    request: QueryRequest | None = None
    options: RequestOptions = field(default_factory=RequestOptions)


@dataclass(frozen=True)
class Deps:
    ctgov: FakeClient
    resolver: EntityResolver


def deps_for(sizes: dict[Params, int]) -> Deps:
    client = FakeClient([], sizes=sizes)
    return Deps(client, EntityResolver(client, Countries(), low_match_threshold=10))


def plan_of(*entities: Entity, phases: list[str] | None = None, year_from: int | None = None) -> QueryPlan:
    filters = PUBLIC_PLAN.filters.model_copy(update={"phases": phases or [], "year_from": year_from})
    return PUBLIC_PLAN.model_copy(update={"entities": list(entities), "filters": filters})


def drug(text: str, role: str = "filter") -> Entity:
    return Entity(kind="drug", value=text, role=role)


PEMBRO = {
    search("query.intr", "pembrolizumab"): 2971,
    strict("pembrolizumab"): 2567,
    search("query.cond", "pembrolizumab"): 12,
}


async def test_a_drug_is_counted_under_both_definitions_and_as_a_condition() -> None:
    resolver = deps_for(PEMBRO).resolver

    result = await resolver.resolve("drug", "pembrolizumab", FakeContext(), drug_match="broad")

    assert (result.status, result.trials_matched, result.strict_name_matches) == ("ok", 2971, 2567)
    assert [(r.kind, r.trials_matched) for r in result.other_readings] == [("condition", 12)]
    term = bind_term(result)
    assert (term.parameter, term.expr, term.definition) == ("query.intr", None, "intervention_search")
    assert "2,971" in term.note and "2,567" in term.note


async def test_name_only_matching_uses_the_strict_count_and_an_expression() -> None:
    resolver = deps_for(PEMBRO).resolver

    result = await resolver.resolve("drug", "pembrolizumab", FakeContext(), drug_match="name_only")

    assert (result.trials_matched, result.definition) == (2567, "intervention_name")
    term = bind_term(result)
    assert term.parameter is None and term.expr == essie.area("InterventionName", "pembrolizumab")


async def test_operator_words_in_a_name_are_escaped_before_they_are_searched() -> None:
    sizes = {search("query.cond", "\\ALL"): 4572, search("query.intr", "\\ALL"): 3}
    resolver = deps_for(sizes).resolver

    result = await resolver.resolve("condition", "ALL", FakeContext(), drug_match="broad")

    assert (result.term_searched, result.trials_matched) == ("\\ALL", 4572)


async def test_a_country_is_counted_under_the_registry_spelling() -> None:
    name = "Turkey (Türkiye)"
    deps = deps_for({Params(advanced=essie.area("LocationCountry", name)): 700})

    result = await deps.resolver.resolve("country", "Turkey", FakeContext(), drug_match="broad")

    assert (result.term_searched, result.trials_matched, result.definition) == (name, 700, "country_exact")
    assert bind_term(result).expr == essie.area("LocationCountry", name)


@pytest.mark.parametrize(
    ("count", "status"), [(0, "no_match"), (5, "low_match"), (10, "ok"), (1_000_000, "matches_everything")]
)
async def test_the_status_follows_the_count(count: int, status: str) -> None:
    deps = deps_for({search("query.intr", "x"): count})

    result = await deps.resolver.resolve("drug", "x", FakeContext(), drug_match="broad")

    assert result.status == status


async def test_resolving_builds_a_scope_with_filters_probes_it_and_warns_about_a_misspelling() -> None:
    sizes = {search("query.intr", "pembrolizumb"): 5}
    plan = plan_of(drug("pembrolizumb"), phases=["PHASE3"], year_from=2015)
    deps = deps_for(
        {
            **sizes,
            Params(
                (("query.intr", "pembrolizumb"),),
                essie.and_(essie.any_of("Phase", ["PHASE3"]), essie.range_("StartDate", "2015-01-01", None)),
            ): 4,
        }
    )
    ctx = FakeContext()

    resolved = await resolve_entities(Planned(plan), deps, ctx)

    assert isinstance(resolved, Resolved)
    (scope,) = resolved.scopes
    assert scope.params().pairs() == (
        ("query.intr", "pembrolizumb"),
        ("filter.advanced", "(AREA[Phase]PHASE3) AND (AREA[StartDate]RANGE[2015-01-01,MAX])"),
    )
    assert resolved.matched == {"s0": 4}
    assert [note.code for note in resolved.warnings] == ["low_match_count"]
    assert [entity.source for entity in resolved.entities] == ["plan"]
    assert [request.origin for request in ctx.requests][-1] == "probe"


async def test_a_date_range_applies_to_the_date_on_the_axis_when_the_plan_names_none() -> None:
    plan = plan_of(drug("pembrolizumab"), year_from=2015).model_copy(
        update={
            "analysis": Aggregate(
                kind="aggregate",
                dimension="completion_date",
                series=None,
                time_unit="year",
                top_n=None,
                statistic=None,
                of=None,
            )
        }
    )

    completed_since_2015 = Params(
        (("query.intr", "pembrolizumab"),), essie.range_("CompletionDate", "2015-01-01", None)
    )

    resolved = await resolve_entities(
        Planned(plan), deps_for({**PEMBRO, completed_since_2015: 9}), FakeContext()
    )

    assert isinstance(resolved, Resolved)
    assert resolved.scopes[0].date_range == DateRange("CompletionDate", "2015-01-01", None)


async def test_compared_entities_become_one_scope_each_and_an_empty_one_is_kept() -> None:
    sizes = {
        search("query.intr", "a"): 50, search("query.cond", "a"): 0, strict("a"): 40,
        search("query.intr", "b"): 0, search("query.cond", "b"): 0, strict("b"): 0,
    }  # fmt: skip
    plan = plan_of(drug("a", "compare"), drug("b", "compare"))

    resolved = await resolve_entities(Planned(plan), deps_for(sizes), FakeContext())

    assert isinstance(resolved, Resolved)
    assert [(scope.id, scope.label) for scope in resolved.scopes] == [("s0", "a"), ("s1", "b")]
    assert resolved.matched == {"s0": 50, "s1": 0}  # the group that matches nothing stays, as an empty series


async def test_a_filter_entity_that_matches_nothing_is_no_data_that_names_the_other_reading() -> None:
    sizes = {search("query.intr", "asthma"): 0, search("query.cond", "asthma"): 4000, strict("asthma"): 0}

    outcome = await resolve_entities(Planned(plan_of(drug("asthma"))), deps_for(sizes), FakeContext())

    assert isinstance(outcome, Outcome) and outcome.kind == "no_data"
    assert "No trials match 'asthma' as a drug" in outcome.message and "4,000" in outcome.message


async def test_an_entity_that_matches_the_whole_registry_is_a_clarification() -> None:
    sizes = {search("query.intr", "x"): 1_000_000}

    outcome = await resolve_entities(Planned(plan_of(drug("x"))), deps_for(sizes), FakeContext())

    assert isinstance(outcome, Outcome) and (outcome.kind, outcome.reason) == (
        "clarification",
        "could_not_interpret",
    )


async def test_an_empty_combination_says_what_matches_without_each_part() -> None:
    both = Params((("query.intr", "x"),), essie.any_of("Phase", ["PHASE3"]))
    sizes = {
        search("query.intr", "x"): 90, search("query.cond", "x"): 0, strict("x"): 80,
        both: 0,
        Params(advanced=essie.any_of("Phase", ["PHASE3"])): 5000,
    }  # fmt: skip
    plan = plan_of(drug("x"), phases=["PHASE3"])

    outcome = await resolve_entities(Planned(plan), deps_for(sizes), FakeContext())

    assert isinstance(outcome, Outcome)
    assert outcome.message == (
        "No trials match all of these together. Without 'x', 5,000 trials match. "
        "Without the phase filter, 90 trials match."
    )
