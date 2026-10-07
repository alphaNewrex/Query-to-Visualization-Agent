"""`check_invariants` accepts a sound response and names the rule a corruption breaks."""

from collections.abc import Callable
from typing import Any

from ctviz.contract.invariants import check_invariants
from ctviz.contract.response import VisualizationResponse
from tests.unit.contract_samples import RESPONSES, time_series_response


class FakeProvenance:
    """The registry as one request saw it: two trials, their start dates, and one logged count."""

    def __init__(self, counts: dict[str, int] | None = None) -> None:
        self.counts = counts or {}

    def was_returned(self, nct_id: str) -> bool:
        return nct_id in {"NCT00000001", "NCT00000002"}

    def value_at(self, nct_id: str, path: str) -> str | None:
        return {"NCT00000001": "2015-01", "NCT00000002": "2016-01"}[nct_id]

    def total_count(self, url: str) -> int | None:
        return self.counts.get(url)


def violations(document: dict[str, Any], provenance: FakeProvenance | None = None) -> list[str]:
    response = RESPONSES.validate_python(document)
    assert isinstance(response, VisualizationResponse)
    return check_invariants(response, provenance or FakeProvenance())


def rules(found: list[str]) -> set[str]:
    return {line.split(":")[0] for line in found}


def corrupt(change: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    document = time_series_response()
    change(document)
    return document


def test_a_sound_response_has_no_violations() -> None:
    assert violations(time_series_response()) == []


def test_rule_1_an_empty_visualization() -> None:
    document = corrupt(lambda d: d["visualization"].update(data=[]))

    assert "1" in rules(violations(document))


def test_rule_2_a_row_lacks_an_encoded_field() -> None:
    document = corrupt(lambda d: d["visualization"]["data"][1].pop("trial_count"))

    assert "2" in rules(violations(document))


def test_rule_4_a_quantitative_value_is_not_a_number() -> None:
    document = corrupt(lambda d: d["visualization"]["data"][1].update(trial_count="3"))

    assert "4" in rules(violations(document))


def test_rule_5_periods_with_a_gap_or_a_wrong_label() -> None:
    gap = corrupt(lambda d: d["visualization"]["data"][2].update(start_year="2018"))
    label = corrupt(lambda d: d["visualization"]["data"][2].update(start_year="2017-Q1"))

    assert "5" in rules(violations(gap))
    assert "5" in rules(violations(label))


def test_rule_6_a_repeated_period() -> None:
    document = corrupt(lambda d: d["visualization"]["data"][2].update(start_year="2016"))

    assert "6" in rules(violations(document))


def test_rule_8_a_stacked_line() -> None:
    document = corrupt(lambda d: d["visualization"].update(stack="stacked"))

    assert "8" in rules(violations(document))


def test_rule_11_references_that_are_not_the_cited_trials() -> None:
    extra = corrupt(lambda d: d["references"].pop("NCT00000002"))
    over_cap = corrupt(lambda d: d["meta"]["citations"].update(max_per_datum=0))

    assert "11" in rules(violations(extra))
    assert "11" in rules(violations(over_cap))


def test_rule_12_counts_that_do_not_reconcile() -> None:
    document = corrupt(lambda d: d["meta"]["counts"]["series"][0].update(trials_matched=13))

    assert rules(violations(document)) >= {"12"}


def test_rule_13_a_drawn_count_that_differs_from_the_citation_count() -> None:
    document = corrupt(lambda d: d["visualization"]["data"][0].update(citation_count=5))

    assert "13" in rules(violations(document))


def test_rule_14_a_trial_the_registry_never_returned() -> None:
    def cite_unknown(d: dict[str, Any]) -> None:
        d["visualization"]["data"][2]["citations"] = [
            {"nct_id": "NCT99999999", "field": "x", "excerpt": None}
        ]
        d["references"]["NCT99999999"] = d["references"]["NCT00000001"]
        d["meta"]["citations"]["trials_cited"] = 3

    assert "14" in rules(violations(corrupt(cite_unknown)))


def test_rule_15_an_excerpt_that_differs_from_the_record() -> None:
    document = corrupt(lambda d: d["visualization"]["data"][0]["citations"][0].update(excerpt="1999-01"))

    assert "15" in rules(violations(document))


def test_rule_16_a_count_that_differs_from_the_logged_total() -> None:
    document = corrupt(lambda d: d["visualization"]["data"][0].update(source_url="https://example.test/q"))

    assert "16" in rules(violations(document, FakeProvenance({"https://example.test/q": 99})))
    assert violations(document, FakeProvenance({"https://example.test/q": 4})) == []
    assert violations(document) == []


def test_rule_17_rows_that_do_not_add_up_to_the_analyzed_trials() -> None:
    def short(d: dict[str, Any]) -> None:
        d["visualization"]["data"][2].update(trial_count=2, citation_count=2)

    assert "17" in rules(violations(corrupt(short)))


def bar_response() -> dict[str, Any]:
    """The time series recast as a bar chart over an ordinal axis of three phases."""
    document = time_series_response()
    domain = ["Phase 1", "Phase 2", "No phase listed"]
    document["visualization"] = {
        "type": "bar_chart", "title": "Trials by phase", "subtitle": None,
        "orientation": "vertical", "stack": "none",
        "encoding": {
            "x": {"field": "phase", "type": "ordinal", "title": "Phase", "domain": domain,
                  "sort": None, "is_exclusive": True},
            "y": {"field": "trial_count", "type": "quantitative", "title": "Trials",
                  "unit": "trials", "format": ",d", "scale": "linear"},
            "series": None, "tooltip": [],
        },
        "data": [
            {**row, "phase": phase}
            for phase, row in zip(domain, document["visualization"]["data"], strict=True)
        ],
    }  # fmt: skip
    for datum in document["visualization"]["data"]:
        datum.pop("start_year")
    return document


def test_a_sound_bar_chart_has_no_violations() -> None:
    assert violations(bar_response()) == []


def test_rule_3_a_category_outside_the_domain() -> None:
    document = bar_response()
    document["visualization"]["data"][0]["phase"] = "Phase 9"

    assert {"3", "6"} <= rules(violations(document))


def test_rule_7_bar_rows_out_of_domain_order() -> None:
    document = bar_response()
    data = document["visualization"]["data"]
    data[0]["phase"], data[1]["phase"] = data[1]["phase"], data[0]["phase"]

    assert rules(violations(document)) == {"7"}
