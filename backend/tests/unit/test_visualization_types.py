"""Each of the seven visualization types accepts the shape section 5.4 prints and rejects a malformed one."""

from copy import deepcopy
from typing import Any, get_args

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from ctviz.contract.plan import ChartType
from ctviz.contract.response import (
    BarChart,
    Datum,
    Histogram,
    Metric,
    NetworkGraph,
    ScatterPlot,
    Table,
    TimeSeries,
    Visualization,
)

VISUALIZATIONS: TypeAdapter[Any] = TypeAdapter(Visualization)

PHASES = [
    "Early Phase 1",
    "Phase 1",
    "Phase 1/Phase 2",
    "Phase 2",
    "Phase 2/Phase 3",
    "Phase 3",
    "Phase 4",
    "Not Applicable",
    "No phase listed",
]
CITATION = {"nct_id": "NCT00759876", "field": "protocolSection.designModule.phases", "excerpt": None}


def quantitative(
    field: str, title: str, *, unit: str | None = "trials", scale: str = "linear"
) -> dict[str, Any]:
    return {
        "field": field,
        "type": "quantitative",
        "title": title,
        "unit": unit,
        "format": ",d",
        "scale": scale,
    }


def category(field: str, title: str, domain: list[str], *, exclusive: bool = True) -> dict[str, Any]:
    return {
        "field": field,
        "type": "nominal",
        "title": title,
        "domain": domain,
        "sort": None,
        "is_exclusive": exclusive,
    }


def datum(**keys: Any) -> dict[str, Any]:
    """A row with the three reserved keys, citing one trial."""
    return {"citations": [CITATION], "citation_count": 1, "source_url": None, **keys}


BAR_CHART: dict[str, Any] = {
    "type": "bar_chart",
    "title": "Trials by phase: Duchenne muscular dystrophy",
    "subtitle": "499 trials · ClinicalTrials.gov, data as of 2026-10-06",
    "orientation": "vertical",
    "stack": "none",
    "encoding": {
        "x": {
            **category("phase", "Phase", PHASES),
            "type": "ordinal",
            "sort": {"by": "natural", "order": "ascending"},
        },
        "y": quantitative("trial_count", "Trials"),
        "series": None,
        "tooltip": [
            {
                "field": "share",
                "title": "Share of trials",
                "type": "quantitative",
                "unit": None,
                "format": ".1%",
                "href_field": None,
            }
        ],
    },
    "data": [datum(phase="No phase listed", trial_count=142, share=0.2846, citation_count=142)],
}

HISTOGRAM: dict[str, Any] = {
    "type": "histogram",
    "title": "Enrollment of pembrolizumab trials",
    "subtitle": None,
    "encoding": {
        "x": quantitative("bin_start", "Enrollment", unit="participants"),
        "x2": {"field": "bin_end"},
        "y": quantitative("trial_count", "Trials"),
        "label": {"field": "bin_label"},
        "tooltip": [],
    },
    "data": [
        datum(bin_start=5000, bin_end=10000, bin_label="5000-9999", trial_count=3, citation_count=3),
        datum(bin_start=10000, bin_end=None, bin_label="10000+", trial_count=4, citation_count=4),
    ],
}

SCATTER_PLOT: dict[str, Any] = {
    "type": "scatter_plot",
    "title": "Enrollment against duration",
    "subtitle": None,
    "encoding": {
        "x": quantitative("enrollment", "Participants", unit="participants", scale="log"),
        "y": quantitative("duration_months", "Duration", unit="months"),
        "series": category("phase", "Phase", ["Phase 2"]),
        "size": None,
        "label": None,
        "tooltip": [],
    },
    "data": [datum(nct_id="NCT00759876", enrollment=616, duration_months=24, phase="Phase 2")],
}

NETWORK_GRAPH: dict[str, Any] = {
    "type": "network_graph",
    "title": "Sponsors and the drugs they test: Duchenne muscular dystrophy",
    "subtitle": None,
    "is_directed": False,
    "layout": "bipartite",
    "encoding": {
        "nodes": {
            "label": {"field": "label"},
            "color": category("entity_type", "Node type", ["sponsor", "drug"]),
            "size": quantitative("trial_count", "Trials"),
            "tooltip": [],
        },
        "edges": {
            "weight": quantitative("trial_count", "Trials of this drug led by this sponsor"),
            "tooltip": [],
        },
    },
    "data": {
        "nodes": [
            datum(
                id="sponsor:PTC Therapeutics", label="PTC Therapeutics", entity_type="sponsor", trial_count=18
            ),
            datum(id="drug:ataluren", label="Ataluren", entity_type="drug", trial_count=13),
        ],
        "edges": [
            datum(
                id="sponsor:PTC Therapeutics|drug:ataluren",
                source="sponsor:PTC Therapeutics",
                target="drug:ataluren",
                trial_count=13,
            )
        ],
    },
}

TABLE: dict[str, Any] = {
    "type": "table",
    "title": "The largest lung cancer trials",
    "subtitle": None,
    "encoding": {
        "columns": [
            {
                "field": "title",
                "title": "Title",
                "type": "nominal",
                "unit": None,
                "format": None,
                "href_field": "url",
            },
            {
                "field": "enrollment",
                "title": "Enrollment",
                "type": "quantitative",
                "unit": "participants",
                "format": ",d",
                "href_field": None,
            },
        ]
    },
    "data": [
        datum(
            nct_id="NCT00759876",
            title="A trial",
            url="https://clinicaltrials.gov/study/NCT00759876",
            enrollment=616,
        )
    ],
}

METRIC: dict[str, Any] = {
    "type": "metric",
    "title": "Recruiting trials: Duchenne muscular dystrophy",
    "subtitle": None,
    "encoding": {"value": quantitative("trial_count", "Trials")},
    "data": [datum(trial_count=1, citation_count=1)],
}

TIME_SERIES: dict[str, Any] = {
    "type": "time_series",
    "title": "Trials started per year",
    "subtitle": None,
    "mark": "area",
    "stack": "stacked",
    "encoding": {
        "x": {"field": "start_quarter", "type": "temporal", "title": "Start quarter", "time_unit": "quarter"},
        "y": quantitative("trial_count", "Trials started"),
        "series": category("sponsor_class", "Sponsor class", ["Industry"]),
        "tooltip": [],
    },
    "data": [datum(start_quarter="2024-Q2", sponsor_class="Industry", trial_count=1)],
}

EXAMPLES: list[tuple[type[BaseModel], dict[str, Any]]] = [
    (BarChart, BAR_CHART),
    (TimeSeries, TIME_SERIES),
    (Histogram, HISTOGRAM),
    (ScatterPlot, SCATTER_PLOT),
    (NetworkGraph, NETWORK_GRAPH),
    (Table, TABLE),
    (Metric, METRIC),
]


@pytest.mark.parametrize(("model", "document"), EXAMPLES, ids=[model.__name__ for model, _ in EXAMPLES])
def test_a_type_is_chosen_by_its_tag_and_every_key_survives_a_round_trip(
    model: type[BaseModel], document: dict[str, Any]
) -> None:
    visualization = VISUALIZATIONS.validate_python(document)

    assert isinstance(visualization, model)
    assert VISUALIZATIONS.dump_python(visualization, mode="json") == document


def test_the_chart_preferences_of_the_plan_are_exactly_the_visualization_types() -> None:
    tags = {get_args(model.model_fields["type"].annotation)[0] for model, _ in EXAMPLES}

    assert tags == set(get_args(ChartType))


def test_a_metric_has_exactly_one_row() -> None:
    document = deepcopy(METRIC)
    document["data"].append(datum(trial_count=2, citation_count=2))

    with pytest.raises(ValidationError):
        VISUALIZATIONS.validate_python(document)


def test_a_graph_is_never_directed_in_v1() -> None:
    document = deepcopy(NETWORK_GRAPH)
    document["is_directed"] = True

    with pytest.raises(ValidationError):
        VISUALIZATIONS.validate_python(document)


def test_an_unknown_key_in_an_encoding_is_rejected() -> None:
    document = deepcopy(BAR_CHART)
    document["encoding"]["y"]["colour"] = "red"

    with pytest.raises(ValidationError):
        VISUALIZATIONS.validate_python(document)


def test_a_log_scale_or_a_number_format_outside_the_closed_sets_is_rejected() -> None:
    scale = deepcopy(BAR_CHART)
    scale["encoding"]["y"]["scale"] = "sqrt"
    number_format = deepcopy(BAR_CHART)
    number_format["encoding"]["y"]["format"] = ".3f"

    for document in (scale, number_format):
        with pytest.raises(ValidationError):
            VISUALIZATIONS.validate_python(document)


def test_a_row_keeps_its_reserved_keys_apart_from_the_keys_the_encoding_names() -> None:
    row = Datum.model_validate(datum(phase="Phase 2", trial_count=3, citation_count=3))

    assert (row.citation_count, row.source_url, len(row.citations)) == (3, None, 1)
    assert row.model_extra == {"phase": "Phase 2", "trial_count": 3}


def test_a_row_without_a_citation_count_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Datum.model_validate({"phase": "Phase 2", "trial_count": 3})
