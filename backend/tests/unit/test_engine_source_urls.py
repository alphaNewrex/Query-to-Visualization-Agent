"""The `source_url` of a bar: the same trials as the bar, or no URL at all."""

from typing import Any
from urllib.parse import parse_qsl, urlparse

import pytest

from ctviz.catalog.fields import Window
from ctviz.contract.response import Outcome
from ctviz.ctgov import essie
from ctviz.ctgov.params import BoundTerm, Params, Scope
from ctviz.engine.execute import execute_plan
from ctviz.engine.lower import EnginePlan
from ctviz.engine.strategy import Limits, choose_strategy

from .engine_memory import Memory, Registry, answer, dim, matches, plan, public, record, rows_of, scope

pytestmark = pytest.mark.anyio

STATES = public(
    {
        "kind": "aggregate",
        "dimension": "state",
        "series": None,
        "time_unit": None,
        "top_n": 15,
        "statistic": None,
        "of": None,
    }
)


def country_scope(name: str) -> Scope:
    term = BoundTerm(
        kind="country",
        text=name,
        term=name,
        parameter=None,
        expr=essie.area("LocationCountry", name),
        definition="country_exact",
        note="",
    )
    return scope(None, "s0", terms=(term,))


def trials_of(url: str, studies: list[Any]) -> set[str]:
    """The trials a URL selects, read the way the registry reads its search (the harness's model)."""
    pairs = parse_qsl(urlparse(url).query)
    texts = tuple((name, value) for name, value in pairs if name.startswith(("query.", "filter.overall")))
    advanced = next((essie.Expr(v) for n, v in pairs if n == "filter.advanced"), None)
    params = Params(texts, advanced)
    return {study.nct_id for study in studies if matches(study, params)}


def state_rows(response: Any) -> dict[str, tuple[int, str | None]]:
    items = response.visualization.data
    return {row.model_extra["state"]: (row.citation_count, row.source_url) for row in items}


async def test_a_states_url_holds_the_country_and_the_state_at_one_site() -> None:
    studies = [
        record(1, locations=[("Spain", "Madrid")]),
        # Madrid is in Argentina here, and the trial has a Spanish site elsewhere: not Spain's Madrid.
        record(2, locations=[("Spain", "Cataluña"), ("Argentina", "Madrid")]),
    ]
    p = plan(dim("state"), pub=STATES, scopes=(country_scope("Spain"),))

    run = await answer(p, studies)

    count, url = state_rows(run.response)["Madrid"]
    assert count == 1 and url is not None
    assert trials_of(url, studies) == {"NCT00000001"}


async def test_state_names_that_differ_in_case_accents_or_punctuation_are_one_bar() -> None:
    studies = [
        record(1, locations=[("Spain", "Málaga")]),
        record(2, locations=[("Spain", "Malaga")]),
        record(3, locations=[("Spain", "Malaga")]),
        record(4, locations=[("Spain", "MALAGA"), ("Spain", "Málaga")]),
        record(5, locations=[("Spain", "A Coruña")]),
        record(6, locations=[("Spain", "A Coruna")]),
    ]
    p = plan(dim("state"), pub=STATES, scopes=(country_scope("Spain"),))

    run = await answer(p, studies)

    rows = state_rows(run.response)
    # One Malaga of four trials, shown under the spelling the records use most; a trial that writes it two
    # ways is counted once.
    assert rows["Malaga"][0] == 4 and "Málaga" not in rows and "MALAGA" not in rows
    assert rows["A Coruna"][0] == 2  # a tie in spelling goes to the alphabetically first
    for name, (count, url) in rows.items():
        assert url is not None and len(trials_of(url, studies)) == count, name
    assert any("differ only in case, accents or punctuation" in a for a in run.response.meta.assumptions)


class _Looser(Registry):
    """A registry that finds more trials for `Madrid` than the walk grouped under that exact name."""

    async def count(self, params: Params, ctx: Any, *, origin: Any) -> int:
        total = await super().count(params, ctx, origin=origin)
        return total + 1 if '"madrid"' in str(params.advanced) else total


async def test_a_state_search_that_returns_other_trials_than_the_bar_is_withheld() -> None:
    studies = [
        record(1, locations=[("Spain", "Madrid")]),
        record(2, locations=[("Spain", "Madrid")]),
        record(3, locations=[("Spain", "Sevilla")]),
    ]
    p = plan(dim("state"), pub=STATES, scopes=(country_scope("Spain"),))

    client, ctx = _Looser(studies), Memory()
    matched = {"s0": await client.count(p.scopes[0].params(), ctx, origin="probe")}
    xp = choose_strategy(p, matched, Limits(1000, 300), prefer_walk=False)
    assert not isinstance(xp, Outcome)
    result = await execute_plan(p, xp, client, ctx)

    cells = result.frames[0].cells
    assert cells[("madrid",)].trials == 2 and cells[("madrid",)].source_url is None
    assert cells[("sevilla",)].source_url is not None


async def test_a_cell_that_no_trial_fell_into_has_the_url_of_a_zero_count() -> None:
    studies = [record(1, phases=("PHASE2",)), record(2, phases=("PHASE3",))]
    analysis = {
        "kind": "aggregate",
        "dimension": "phase",
        "series": None,
        "time_unit": None,
        "top_n": 15,
        "statistic": None,
        "of": None,
    }
    p = plan(dim("phase"), pub=public(analysis))

    run = await answer(p, studies)

    rows = run.response.visualization.data
    zero = [row for row in rows if row.citation_count == 0]
    assert zero, "the phases nobody has are drawn with a zero"
    for row in rows:
        assert row.source_url is not None, row.model_extra
        assert len(trials_of(row.source_url, studies)) == row.citation_count


async def test_the_zero_periods_of_a_date_axis_have_a_url_too() -> None:
    studies = [record(1, start="2020-03"), record(2, start="2022-04")]
    analysis = {
        "kind": "aggregate",
        "dimension": "start_date",
        "series": None,
        "time_unit": "year",
        "top_n": None,
        "statistic": None,
        "of": None,
    }
    p: EnginePlan = plan(dim("start_date"), pub=public(analysis), window=Window("year", "2020", "2022"))

    run = await answer(p, studies)

    assert {row["start_year"]: row["trial_count"] for row in rows_of(run.response)} == {
        "2020": 1,
        "2021": 0,
        "2022": 1,
    }
    for row in run.response.visualization.data:
        assert row.source_url is not None
        assert len(trials_of(row.source_url, studies)) == row.citation_count


async def test_a_statistic_and_the_rest_row_say_why_they_have_no_url() -> None:
    from ctviz.engine.lower import MeasureSpec

    studies = [record(n, sponsor=f"S{n}", enrollment=n) for n in range(1, 6)]
    analysis = {
        "kind": "aggregate",
        "dimension": "sponsor",
        "series": None,
        "time_unit": None,
        "top_n": 2,
        "statistic": "median",
        "of": "enrollment",
    }
    counted = {**analysis, "statistic": None, "of": None}

    measured = await answer(
        plan(dim("sponsor"), pub=public(analysis), top_n=2, measure=MeasureSpec("median", "enrollment")),
        studies,
    )
    plain = await answer(plan(dim("sponsor"), pub=public(counted), top_n=2), studies)
    exact = await answer(plan(dim("phase"), pub=public({**counted, "dimension": "phase"})), studies)

    reasons = measured.response.meta.citations.source_url_reasons
    assert any("A statistic is computed here" in sentence for sentence in reasons)
    assert any(
        "'Other' row sums" in sentence for sentence in measured.response.meta.citations.source_url_reasons
    )
    assert [s for s in plain.response.meta.citations.source_url_reasons if "'Other' row" in s]
    # Every row of an exact grouping has a URL, so there is nothing to explain.
    assert exact.response.meta.citations.source_url_reasons == []
