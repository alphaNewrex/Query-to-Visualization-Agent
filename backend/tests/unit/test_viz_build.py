"""The builders turn hand-made frames into responses that validate and pass every invariant."""

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import TypeAdapter

from ctviz.catalog.fields import BoundDimension, Evidence, FieldSpec, Window
from ctviz.catalog.vocab import ENROLLMENT_BINS
from ctviz.contract.invariants import check_invariants
from ctviz.contract.plan import QueryPlan
from ctviz.contract.request import QueryRequest, RequestOptions
from ctviz.contract.response import (
    AppliedFilters,
    CacheInfo,
    Clarification,
    ClarificationResponse,
    MessageResponse,
    Outcome,
    PlannerInfo,
    QueryResponse,
    Table,
    Timing,
    TruncationItem,
    VisualizationResponse,
)
from ctviz.ctgov.client import ApiVersion
from ctviz.ctgov.params import Scope
from ctviz.engine.frame import Cell, Frame, TrialEvidence
from ctviz.engine.lower import EnginePlan, ListRows, PointRows
from ctviz.viz.meta import MetaContext, build_response, outcome_response
from ctviz.viz.shaped import NetworkEdge, NetworkNode, ShapedResult, TrialRow
from tests.unit.contract_samples import PLAN

VERSION = ApiVersion(api_version="2.0.5", data_timestamp="2026-10-06T09:00:05")
START = "protocolSection.statusModule.startDateStruct.date"
PHASES = "protocolSection.designModule.phases[0]"
RESPONSES: TypeAdapter[Any] = TypeAdapter(QueryResponse)


class Records:
    """A stand-in for the request's memory: every trial was returned and every excerpt is what was cited."""

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str | None] = {}

    def remember(self, nct_id: str, evidence: Evidence) -> None:
        self.values[(nct_id, evidence.path)] = evidence.excerpt

    def was_returned(self, nct_id: str) -> bool:
        return True

    def value_at(self, nct_id: str, path: str) -> str | None:
        return self.values.get((nct_id, path))

    def total_count(self, url: str) -> int | None:
        return None


RECORDS = Records()


def spec(key: str, title: str, kind: str, *, exclusive: bool = True, ordinal: bool = False) -> FieldSpec:
    return FieldSpec(
        key=key,
        title=title,
        kind=kind,  # type: ignore[arg-type]
        pieces=("StartDate",),
        extract=lambda study, context, dim: (),
        is_exclusive=exclusive,
        is_ordinal=ordinal,
    )


START_YEAR = BoundDimension(spec("start_date", "Start date", "date", ordinal=True), "year", "axis")
PHASE = BoundDimension(spec("phase", "Phase", "category", ordinal=True), None, "axis")
COUNTRY = BoundDimension(spec("country", "Country", "entity", exclusive=False), None, "axis")
STATUS = BoundDimension(spec("overall_status", "Status", "category"), None, "series")
ENROLLMENT = BoundDimension(spec("enrollment", "Enrollment", "number", ordinal=True), None, "axis")
SPONSOR = BoundDimension(spec("sponsor", "Sponsor", "entity"), None, "node")
DRUG = BoundDimension(spec("drug", "Drug", "entity", exclusive=False), None, "node")


def scope(index: int = 0, label: str | None = "pembrolizumab") -> Scope:
    return Scope(id=f"s{index}", label=label, terms=(), enum_filters={}, date_range=None)


def trial(n: int, path: str = START, excerpt: str = "2015-03") -> TrialEvidence:
    nct_id = f"NCT{n:08d}"
    evidence = Evidence(path, excerpt)
    RECORDS.remember(nct_id, evidence)
    return TrialEvidence(nct_id, (evidence,), (True, "2020-01-01", nct_id))


def cell(key: tuple[str, ...], trials: int, *sample: TrialEvidence) -> Cell:
    return Cell(key=key, labels=key, trials=trials, sample=list(sample))


def frame(
    label: str | None, dims: tuple[BoundDimension, ...], cells: list[Cell], *, trials: int | None = None
) -> Frame:
    total = trials if trials is not None else sum(c.trials for c in cells) if dims else cells[0].trials
    return Frame(
        scope=scope(0, label),
        dims=dims,
        sample_size=5,
        cells={c.key: c for c in cells},
        matched=total,
        seen=total,
        analyzed=total,
    )


def plan_of(**changes: Any) -> EnginePlan:
    values: dict[str, Any] = {
        "scopes": (scope(),),
        "compare_kind": None,
        "dimensions": (),
        "relation": None,
        "pairing": "same_trial",
        "rows": None,
        "window": None,
        "top_n": 15,
        "max_series": 9,
        "min_link_weight": 2,
        "max_links": 60,
        "chart_preference": None,
        "citations_per_datum": 5,
        "public": QueryPlan.model_validate(PLAN),
    }
    return EnginePlan(**{**values, **changes})


def context(**changes: Any) -> MetaContext:
    values: dict[str, Any] = {
        "request_id": "0f3c7c1e-0000-0000-0000-000000000000",
        "generated_at": datetime(2026, 10, 6, 12, tzinfo=UTC),
        "query": "a question",
        "request": QueryRequest(query="a question"),
        "filters": AppliedFilters.model_validate(
            {
                "drug_name": [],
                "condition": [],
                "sponsor": [],
                "country": [],
                "term": [],
                "trial_phase": [],
                "status": [],
                "study_type": [],
                "sponsor_class": [],
                "intervention_type": [],
                "sex": [],
                "age_group": [],
                "allocation": [],
                "masking": [],
                "primary_purpose": [],
                "has_results": [],
                "intervention_model": [],
                "start_year": None,
                "end_year": None,
                "date_field": None,
                "compare": None,
                "exclude": {
                    "drug_name": [],
                    "condition": [],
                    "sponsor": [],
                    "country": [],
                    "term": [],
                    "status": [],
                },
            }
        ),
        "plan": QueryPlan.model_validate(PLAN),
        "options": RequestOptions(),
        "planner": PlannerInfo(
            mode="llm",
            model=None,
            reasoning_effort=None,
            prompt_version="plan-v1",
            attempts=1,
            is_repaired=False,
            is_fallback=False,
            usage=None,
        ),
        "timing": Timing(total_ms=1, plan_ms=1, resolve_ms=1, fetch_ms=1, build_ms=0),
        "cache": CacheInfo(is_plan_cached=False, is_response_cached=False, cached_at=None),
    }
    return MetaContext(**{**values, **changes})


def respond(plan: EnginePlan, shaped: ShapedResult) -> VisualizationResponse:
    response = build_response(context(), plan, shaped, VERSION, titles={})
    assert isinstance(response, VisualizationResponse), response.message
    RESPONSES.validate_python(response.model_dump(mode="json"))
    assert check_invariants(response, RECORDS) == []
    return response


def rows(response: VisualizationResponse) -> list[dict[str, Any]]:
    data = response.visualization.data
    assert isinstance(data, list)
    return [{k: v for k, v in (row.model_extra or {}).items()} for row in data]


def test_time_series_fills_the_window_and_cites_each_period() -> None:
    cells = [cell(("2015",), 4, trial(1), trial(2)), cell(("2017",), 3, trial(3))]
    plan = plan_of(dimensions=(START_YEAR,), window=Window("year", "2015", "2018"))
    response = respond(plan, ShapedResult(frames=(frame("pembrolizumab", (START_YEAR,), cells),)))

    viz = response.visualization
    assert viz.type == "time_series"
    assert [r["start_year"] for r in rows(response)] == ["2015", "2016", "2017", "2018"]
    assert [r["trial_count"] for r in rows(response)] == [4, 0, 3, 0]
    assert viz.title == "Trials started per year: pembrolizumab"
    assert response.message == "0 trials started in 2018; the peak was 4 in 2015."
    assert set(response.references) == {"NCT00000001", "NCT00000002", "NCT00000003"}
    assert response.meta.interpretation is not None
    assert response.meta.interpretation.chart_rationale.startswith("Rule 5")


def test_partial_period_warning_when_the_data_date_is_in_the_last_period() -> None:
    cells = [cell(("2025",), 4, trial(1)), cell(("2026",), 2, trial(2))]
    plan = plan_of(dimensions=(START_YEAR,), window=Window("year", "2025", "2026"))
    response = respond(plan, ShapedResult(frames=(frame("pembrolizumab", (START_YEAR,), cells),)))
    assert [w.code for w in response.meta.warnings] == ["partial_period"]


def test_ordinal_bar_chart_is_vertical_in_domain_order_with_shares() -> None:
    cells = [
        cell(("Phase 1",), 6, trial(1, PHASES, "PHASE1")),
        cell(("Phase 2",), 4, trial(2, PHASES, "PHASE2")),
    ]
    plan = plan_of(dimensions=(PHASE,))
    response = respond(plan, ShapedResult(frames=(frame("pembrolizumab", (PHASE,), cells),)))

    viz = response.visualization
    assert viz.type == "bar_chart"
    assert viz.orientation == "vertical"
    assert viz.encoding.x.domain == ["Phase 1", "Phase 2"]
    assert [r["share"] for r in rows(response)] == [0.6, 0.4]


def test_exclusive_series_on_a_nominal_axis_is_stacked_and_the_grid_is_full() -> None:
    cells = [
        cell(("Phase 1", "Completed"), 5, trial(1)),
        cell(("Phase 1", "Recruiting"), 3, trial(2)),
        cell(("Phase 2", "Completed"), 2, trial(3)),
    ]
    dims = (PHASE, STATUS)  # an exclusive axis, so every dimension drawn partitions the trials
    response = respond(plan_of(dimensions=dims), ShapedResult(frames=(frame("pembrolizumab", dims, cells),)))

    viz = response.visualization
    assert viz.type == "bar_chart"
    assert viz.stack == "stacked"
    assert len(rows(response)) == 4  # Phase 2 x Recruiting is zero-filled
    assert {"trial_count": rows(response)[-1]["trial_count"]} == {"trial_count": 0}


def test_comparison_is_grouped_and_never_stacked() -> None:
    drug_a = frame(
        "pembrolizumab", (COUNTRY,), [cell(("China",), 5, trial(1)), cell(("Japan",), 2, trial(2))]
    )
    drug_b = frame("nivolumab", (COUNTRY,), [cell(("China",), 1, trial(3))])
    plan = plan_of(
        scopes=(scope(0, "pembrolizumab"), scope(1, "nivolumab")), compare_kind="drug", dimensions=(COUNTRY,)
    )
    response = respond(plan, ShapedResult(frames=(drug_a, drug_b)))

    viz = response.visualization
    assert viz.type == "bar_chart"
    assert viz.stack == "none"
    assert viz.encoding.series is not None
    assert viz.encoding.series.domain == ["pembrolizumab", "nivolumab"]
    assert viz.encoding.series.is_exclusive is False
    assert [(r["country"], r["group"]) for r in rows(response)][:2] == [
        ("China", "pembrolizumab"),
        ("China", "nivolumab"),
    ]


def test_a_comparison_with_a_split_draws_one_series_per_pair_that_has_trials() -> None:
    ph = BoundDimension(PHASE.spec, None, "series")
    drug_a = frame(
        "pembrolizumab",
        (COUNTRY, ph),
        [cell(("China", "PHASE2"), 4, trial(1)), cell(("China", "PHASE3"), 2, trial(2))],
    )
    drug_b = frame("nivolumab", (COUNTRY, ph), [cell(("China", "PHASE2"), 1, trial(3))])
    plan = plan_of(
        scopes=(scope(0, "pembrolizumab"), scope(1, "nivolumab")),
        compare_kind="drug",
        dimensions=(COUNTRY, ph),
        relation="series",
    )

    response = respond(plan, ShapedResult(frames=(drug_a, drug_b)))

    viz = response.visualization
    assert viz.type == "bar_chart" and viz.stack == "none"
    assert viz.encoding.series is not None and viz.encoding.series.field == "group_split"
    assert viz.encoding.series.title == "Drug and phase"
    assert viz.encoding.series.domain == [
        "pembrolizumab · PHASE2",
        "pembrolizumab · PHASE3",
        "nivolumab · PHASE2",
    ]
    assert viz.title == "Trials by country and phase: pembrolizumab vs nivolumab"
    first = rows(response)[0]
    assert (first["country"], first["group"], first["phase"], first["trial_count"]) == (
        "China",
        "pembrolizumab",
        "PHASE2",
        4,
    )


def test_compared_totals_and_metric() -> None:
    a = frame("pembrolizumab", (), [cell((), 7, trial(1))])
    b = frame("nivolumab", (), [cell((), 3, trial(2))])
    totals = respond(
        plan_of(scopes=(scope(0, "pembrolizumab"), scope(1, "nivolumab")), compare_kind="drug"),
        ShapedResult(frames=(a, b)),
    )
    assert totals.visualization.type == "bar_chart"
    assert totals.message == "pembrolizumab has the most trials: 7."

    metric = respond(plan_of(), ShapedResult(frames=(frame("pembrolizumab", (), [cell((), 7, trial(1))]),)))
    assert metric.visualization.type == "metric"
    assert rows(metric)[0]["trial_count"] == 7


def test_histogram_bins_are_contiguous_and_the_last_is_open() -> None:
    first, last = ENROLLMENT_BINS[0], ENROLLMENT_BINS[-1]
    cells = [
        cell((first.label,), 5, trial(1, "protocolSection.designModule.enrollmentInfo.count", "50")),
        cell((last.label,), 2),
    ]
    shaped = ShapedResult(frames=(frame("pembrolizumab", (ENROLLMENT,), cells),))
    response = respond(plan_of(dimensions=(ENROLLMENT,)), shaped)
    ends = [r["bin_end"] for r in rows(response)]
    assert ends[:-1] == [b.start for b in ENROLLMENT_BINS[1:]]
    assert ends[-1] is None
    counts = [r["trial_count"] for r in rows(response)]
    assert counts[0] == 5
    assert counts[-1] == 2
    assert sum(counts) == 7


def test_table_preference_returns_the_rows_of_the_aggregate() -> None:
    cells = [cell(("2015",), 4, trial(1))]
    plan = plan_of(dimensions=(START_YEAR,), window=Window("year", "2015", "2015"), chart_preference="table")
    response = respond(plan, ShapedResult(frames=(frame("pembrolizumab", (START_YEAR,), cells),)))
    assert response.visualization.type == "table"
    assert response.meta.warnings == []


def test_bar_preference_on_a_time_axis_draws_bars_and_an_impossible_one_is_ignored() -> None:
    cells = [cell(("2015",), 4, trial(1))]
    shaped = ShapedResult(frames=(frame("pembrolizumab", (START_YEAR,), cells),))
    bars = respond(
        plan_of(
            dimensions=(START_YEAR,), window=Window("year", "2015", "2015"), chart_preference="bar_chart"
        ),
        shaped,
    )
    assert bars.visualization.type == "time_series"
    assert bars.visualization.mark == "bar"

    ignored = respond(
        plan_of(
            dimensions=(START_YEAR,), window=Window("year", "2015", "2015"), chart_preference="network_graph"
        ),
        shaped,
    )
    assert [w.code for w in ignored.meta.warnings] == ["chart_preference_ignored"]


def test_network_with_bipartite_layout() -> None:
    sponsor = NetworkNode(
        "sponsor",
        "PTC",
        cell(("PTC",), 3, trial(1, "protocolSection.sponsorCollaboratorsModule.leadSponsor.name", "PTC")),
    )
    drugs = [
        NetworkNode(
            "drug",
            name,
            cell(
                (name,),
                2,
                trial(10 + n, "protocolSection.armsInterventionsModule.interventions[0].name", name),
            ),
        )
        for n, name in enumerate(["Ataluren", "Givinostat", "Vamorolone"])
    ]
    edges = tuple(
        NetworkEdge(sponsor, d, cell((sponsor.cell.key[0], d.cell.key[0]), 2, trial(20 + n)))
        for n, d in enumerate(drugs)
    )
    shaped = ShapedResult(
        frames=(frame("duchenne", (SPONSOR, DRUG), [], trials=9),), nodes=(sponsor, *drugs), edges=edges
    )
    response = respond(plan_of(dimensions=(SPONSOR, DRUG), relation="network"), shaped)
    viz = response.visualization
    assert viz.type == "network_graph"
    assert viz.layout == "bipartite"
    assert viz.encoding.nodes.color is not None
    assert viz.encoding.nodes.color.domain == ["sponsor", "drug"]
    assert response.meta.counts is not None
    assert response.meta.counts.data_points == 7


def test_too_little_cooccurrence_is_no_data() -> None:
    shaped = ShapedResult(frames=(frame("duchenne", (SPONSOR, DRUG), [], trials=9),))
    response = build_response(
        context(), plan_of(dimensions=(SPONSOR, DRUG), relation="network"), shaped, VERSION, titles={}
    )
    assert isinstance(response, MessageResponse)
    assert response.kind == "no_data"
    assert "insufficient_cooccurrence" in [w.code for w in response.meta.warnings]


def test_table_and_scatter_cite_themselves() -> None:
    evidence = (Evidence("protocolSection.designModule.enrollmentInfo.count", "616"),)
    for n in (1, 2):
        RECORDS.remember(f"NCT{n:08d}", evidence[0])
    trials = tuple(
        TrialRow(
            f"NCT{n:08d}",
            f"Trial {n}",
            {"enrollment": 600 + n, "duration_months": 12.5, "phase": "Phase 3"},
            evidence,
            "Phase 3",
        )
        for n in (1, 2)
    )
    table = respond(
        plan_of(rows=ListRows("enrollment", "desc")),
        ShapedResult(frames=(frame("pembrolizumab", (), [cell((), 2)]),), trials=trials),
    )
    assert table.visualization.type == "table"
    assert isinstance(table.visualization, Table)
    assert table.visualization.data[0].source_url == "https://clinicaltrials.gov/api/v2/studies/NCT00000001"

    scatter = respond(
        plan_of(rows=PointRows("enrollment", "duration_months", None)),
        ShapedResult(frames=(frame("pembrolizumab", (), [cell((), 2)]),), trials=trials),
    )
    assert scatter.visualization.type == "scatter_plot"
    assert scatter.visualization.encoding.x.scale == "log"


def test_zero_trials_is_no_data_and_the_outcomes_carry_their_meta() -> None:
    empty = ShapedResult(frames=(frame("pembrolizumab", (PHASE,), []),))
    response = build_response(context(), plan_of(dimensions=(PHASE,)), empty, VERSION, titles={})
    assert isinstance(response, MessageResponse)
    assert response.message == "No trials were found for pembrolizumab."
    assert response.meta.counts is not None

    clarification = Clarification(reason="missing_entity", missing_fields=["drug_name"], options=[])
    outcome = Outcome("clarification", "missing_entity", "Which drug do you mean?", clarification)
    answered = outcome_response(context(plan=None), outcome)
    assert isinstance(answered, ClarificationResponse)
    assert answered.meta.interpretation is None
    RESPONSES.validate_python(answered.model_dump(mode="json"))


def test_truncation_offers_to_show_more() -> None:
    item = TruncationItem(scope="categories", shown=15, total=40, rule="top 15")
    cells = [cell(("Phase 1",), 6, trial(1))]
    shaped = ShapedResult(frames=(frame("pembrolizumab", (PHASE,), cells),), truncation=(item,))
    response = respond(plan_of(dimensions=(PHASE,)), shaped)
    assert response.meta.truncation.is_truncated
    assert [f.label for f in response.meta.suggested_followups] == ["Show the top 30"]


@pytest.mark.parametrize("citations", [0, 2])
def test_citation_cap_is_respected(citations: int) -> None:
    cells = [cell(("2015",), 4, trial(1), trial(2), trial(3))]
    plan = plan_of(
        dimensions=(START_YEAR,), window=Window("year", "2015", "2015"), citations_per_datum=citations
    )
    shaped = ShapedResult(frames=(frame("pembrolizumab", (START_YEAR,), cells),))
    response = build_response(
        context(options=RequestOptions(citations_per_datum=citations)), plan, shaped, VERSION, titles={}
    )
    assert isinstance(response, VisualizationResponse)
    assert len(response.references) == citations
    assert rows(response)[0]["trial_count"] == 4
    assert response.visualization.type == "time_series"
    assert response.visualization.data[0].citation_count == 4
