"""The state dimension: sites grouped by state, within the country the question names."""

from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from ctviz.catalog.fields import CATALOG, BoundDimension
from ctviz.catalog.states import StateScope
from ctviz.ctgov import essie
from ctviz.ctgov.params import BoundTerm, Scope
from ctviz.ctgov.study import Study, parse_study
from ctviz.engine.strategy import ScopeRun
from ctviz.engine.walk import walk_frame
from ctviz.viz.text import state_sites_note

from .engine_support import FakeClient, FakeContext, engine_plan

pytestmark = pytest.mark.anyio

STATE = CATALOG["state"]
SITES = "protocolSection.contactsLocationsModule.locations"


def trial(number: int, *sites: tuple[str, str | None]) -> Study:
    locations: list[dict[str, Any]] = [
        {k: v for k, v in (("country", country), ("state", state)) if v is not None}
        for country, state in sites
    ]
    return parse_study(
        {
            "protocolSection": {
                "identificationModule": {"nctId": f"NCT{number:08d}", "briefTitle": f"Trial {number}"},
                "statusModule": {"studyFirstPostDateStruct": {"date": "2024-01-05"}},
                "contactsLocationsModule": {"locations": locations},
            }
        }
    )


def country_scope(*names: str) -> Scope:
    terms = tuple(
        BoundTerm(
            kind="country", text=name, term=name, parameter=None,
            expr=essie.area("LocationCountry", name), definition="country_exact", note="",
        )
        for name in names
    )  # fmt: skip
    return Scope(id="s0", label=None, terms=terms, enum_filters={}, date_range=None)


def dimension() -> BoundDimension:
    return BoundDimension(spec=STATE, time_unit=None, role="axis")


US, CA, AU = "United States", "Canada", "Australia"
TRIALS = [
    trial(1, (US, "California"), (US, "Texas"), (US, "Ohio")),  # three states: once in each
    trial(2, *[(US, "California")] * 5),  # five sites in one state: once
    trial(3, (US, None), (CA, "Ontario")),  # no US state: excluded under a US scope
    trial(4, (US, "Texas"), (AU, "Victoria")),
    trial(5, (CA, "Ontario"), (AU, "Victoria")),  # no US site at all
    trial(6),  # no sites
]


def test_a_state_is_counted_once_per_trial_and_quotes_its_site() -> None:
    found = STATE.extract(TRIALS[1], StateScope((US,)), dimension())
    [value] = found
    assert (value.key, value.label) == ("California", "California")
    assert [(e.path, e.excerpt) for e in value.evidence] == [
        (f"{SITES}[0].state", "California"),
        (f"{SITES}[0].country", US),
    ]
    assert [v.key for v in STATE.extract(TRIALS[0], StateScope((US,)), dimension())] == [
        "California",
        "Texas",
        "Ohio",
    ]


def test_only_sites_in_the_named_country_are_grouped() -> None:
    assert [v.key for v in STATE.extract(TRIALS[3], StateScope((US,)), dimension())] == ["Texas"]
    assert [v.key for v in STATE.extract(TRIALS[3], StateScope((AU,)), dimension())] == ["Victoria"]
    assert STATE.extract(TRIALS[2], StateScope((US,)), dimension()) == []


def test_with_no_country_named_every_site_is_grouped() -> None:
    assert [v.key for v in STATE.extract(TRIALS[3], StateScope(()), dimension())] == ["Texas", "Victoria"]
    assert [v.key for v in STATE.extract(TRIALS[3], None, dimension())] == ["Texas", "Victoria"]


def test_the_countries_come_from_the_scope_as_registry_names() -> None:
    assert StateScope.of(country_scope("U.S.")).countries == (US,)
    assert StateScope.of(country_scope("the UK", "Canada")).countries == ("United Kingdom", CA)
    assert StateScope.of(country_scope()).countries == ()


def test_a_state_bucket_is_a_quoted_phrase() -> None:
    assert STATE.bucket_for is not None
    bucket = STATE.bucket_for("New York")
    assert bucket is not None and bucket.expr == 'AREA[LocationState]"New York"'


async def test_a_walk_counts_each_trial_once_per_state_and_counts_the_ones_left_out() -> None:
    plan = engine_plan(dimension(), scopes=(country_scope("United States"),))
    run = ScopeRun(plan.scopes[0], 4, "walk", "", ("NCTId",), 1000)
    frame = await walk_frame(run, plan, None, FakeClient(TRIALS), FakeContext())

    assert {key[0]: cell.trials for key, cell in frame.cells.items()} == {
        "California": 2,
        "Texas": 2,
        "Ohio": 1,
    }
    assert frame.analyzed == 3
    assert {reason: item.count for reason, item in frame.excluded.items()} == {"no_state": 1}
    excluded = sum(item.count for item in frame.excluded.values())
    assert frame.analyzed + excluded == frame.seen == 4  # two trials have no US site: not in the scope
    california = frame.cells[("California",)]
    assert california.expr == 'AREA[LocationState]"California"'
    assert california.source_url is not None
    query = parse_qs(urlparse(california.source_url).query)
    assert query["filter.advanced"] == [
        '(AREA[LocationCountry]"United States") AND (AREA[LocationState]"California")'
    ]


def test_the_assumption_names_the_country_or_says_the_states_are_mixed() -> None:
    assert "sites in United States" in state_sites_note((US,), is_open=False)
    assert "no country" in state_sites_note((), is_open=True)
