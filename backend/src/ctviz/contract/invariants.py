"""Semantic rules a valid visualization response obeys (section 5.8 of the plan).

JSON Schema fixes shapes; these rules fix what the shapes mean together. `check_invariants` returns one
sentence per violation, each starting with the rule number, and an empty list for a sound response.
Rules 14 to 16 need what only a running request knows, which `Provenance` provides. Rule 17 is checked
from the response alone and skipped wherever the response cannot show that the values partition the trials.
"""

import re
from collections import Counter
from collections.abc import Iterator, Sequence
from itertools import pairwise
from typing import Final, Protocol

from ctviz.contract.response import (
    BarChart,
    CategoryChannel,
    Citation,
    Datum,
    FieldDef,
    Histogram,
    Metric,
    NetworkGraph,
    QuantitativeChannel,
    ScatterPlot,
    Table,
    TimeSeries,
    VisualizationResponse,
)

RESERVED_GRAPH_FIELDS: Final = frozenset(
    {
        "x",
        "y",
        "vx",
        "vy",
        "fx",
        "fy",
        "index",
        "size",
        "color",
        "type",
        "hidden",
        "highlighted",
        "forceLabel",
        "zIndex",
    }
)
MAX_SERIES_VALUES: Final = 16
COUNT_FIELD: Final = "trial_count"
GROUP_FIELD: Final = "group"  # the series of a comparison: groups are scopes, not a dimension
_RESERVED_KEYS: Final = frozenset({"citations", "citation_count", "source_url"})
_PERIOD: Final = {
    "year": re.compile(r"^(\d{4})$"),
    "quarter": re.compile(r"^(\d{4})-Q([1-4])$"),
    "month": re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$"),
}

type Drawn = BarChart | TimeSeries | Histogram | ScatterPlot | NetworkGraph | Table | Metric


class Provenance(Protocol):
    """What the running request knows about the registry's answers; `RequestContext` satisfies it."""

    def was_returned(self, nct_id: str) -> bool:
        """Whether ClinicalTrials.gov returned this trial during the request."""
        ...

    def value_at(self, nct_id: str, path: str) -> str | None:
        """The excerpt at a path of the record held in memory, as a citation writes it; None if absent."""
        ...

    def total_count(self, url: str) -> int | None:
        """The `totalCount` of a logged upstream request, or None when that URL was never requested."""
        ...


def check_invariants(response: VisualizationResponse, provenance: Provenance) -> list[str]:
    """Every violated rule of a visualization response, empty when it is sound."""
    viz: Drawn = response.visualization
    rows = list(_all_rows(viz))
    return [
        *_rule_1(viz, rows),
        *_rule_2(viz),
        *_rule_3(viz),
        *_rule_4(viz),
        *_rule_5_to_7(viz),
        *_rule_8(viz),
        *_rule_9(viz),
        *_rule_10(viz),
        *_rule_11(response, rows),
        *_rule_12(response),
        *_rule_13(viz, rows),
        *_rule_14_and_15(response, rows, provenance),
        *_rule_16(viz, provenance),
        *_rule_17(response, viz),
    ]


# --- Rows and channels ---------------------------------------------------------------------------------


def _all_rows(viz: Drawn) -> Iterator[Datum]:
    if isinstance(viz, NetworkGraph):
        yield from viz.data.nodes
        yield from viz.data.edges
    else:
        yield from viz.data


def _keys(row: Datum) -> set[str]:
    return set(row.model_extra or {}) | _RESERVED_KEYS


def _value(row: Datum, field: str) -> object:
    return (row.model_extra or {}).get(field)


def _tables(viz: Drawn) -> Iterator[tuple[str, Sequence[Datum], list[str]]]:
    """Each row table with the row keys its encoding names: (label, rows, fields)."""
    match viz:
        case BarChart() | TimeSeries():
            names = [viz.encoding.x.field, viz.encoding.y.field, *_tooltip_fields(viz.encoding.tooltip)]
            if viz.encoding.series:
                names.append(viz.encoding.series.field)
            yield "data", viz.data, names
        case Histogram():
            hist = viz.encoding
            names = [
                hist.x.field,
                hist.x2.field,
                hist.y.field,
                hist.label.field,
                *_tooltip_fields(hist.tooltip),
            ]
            yield "data", viz.data, names
        case ScatterPlot():
            scatter = viz.encoding
            names = [scatter.x.field, scatter.y.field, *_tooltip_fields(scatter.tooltip)]
            names += [c.field for c in (scatter.series, scatter.size) if c]
            if scatter.label:
                names += _tooltip_fields([scatter.label])
            yield "data", viz.data, names
        case Table():
            yield "data", viz.data, _tooltip_fields(viz.encoding.columns)
        case Metric():
            yield "data", viz.data, [viz.encoding.value.field]
        case NetworkGraph():
            nodes, edges = viz.encoding.nodes, viz.encoding.edges
            node_names = [nodes.label.field, *_tooltip_fields(nodes.tooltip)]
            node_names += [c.field for c in (nodes.color, nodes.size) if c]
            edge_names = _tooltip_fields(edges.tooltip) + ([edges.weight.field] if edges.weight else [])
            yield "data.nodes", viz.data.nodes, node_names
            yield "data.edges", viz.data.edges, edge_names


def _tooltip_fields(defs: Sequence[FieldDef]) -> list[str]:
    return [name for d in defs for name in (d.field, d.href_field) if name]


def _category_channels(viz: Drawn) -> Iterator[tuple[CategoryChannel, Sequence[Datum]]]:
    match viz:
        case BarChart() | TimeSeries():
            if isinstance(viz, BarChart):
                yield viz.encoding.x, viz.data
            if viz.encoding.series:
                yield viz.encoding.series, viz.data
        case ScatterPlot():
            if viz.encoding.series:
                yield viz.encoding.series, viz.data
        case NetworkGraph():
            if viz.encoding.nodes.color:
                yield viz.encoding.nodes.color, viz.data.nodes
        case _:
            return


def _quantitative_channels(viz: Drawn) -> Iterator[tuple[QuantitativeChannel, Sequence[Datum]]]:
    match viz:
        case BarChart() | TimeSeries() | Histogram():
            yield viz.encoding.y, viz.data
            if isinstance(viz, Histogram):
                yield viz.encoding.x, viz.data
        case ScatterPlot():
            yield viz.encoding.x, viz.data
            yield viz.encoding.y, viz.data
            if viz.encoding.size:
                yield viz.encoding.size, viz.data
        case Metric():
            yield viz.encoding.value, viz.data
        case NetworkGraph():
            if viz.encoding.nodes.size:
                yield viz.encoding.nodes.size, viz.data.nodes
            if viz.encoding.edges.weight:
                yield viz.encoding.edges.weight, viz.data.edges
        case Table():
            return


# --- Rules 1 to 10: the shape of the data ----------------------------------------------------------------


def _rule_1(viz: Drawn, rows: Sequence[Datum]) -> list[str]:
    nodes = viz.data.nodes if isinstance(viz, NetworkGraph) else rows
    return [] if nodes else ["1: a visualization needs at least one datum; with none the kind is no_data"]


def _rule_2(viz: Drawn) -> list[str]:
    problems = []
    for label, rows, names in _tables(viz):
        for index, row in enumerate(rows):
            missing = sorted(set(names) - _keys(row))
            if missing:
                problems.append(f"2: {label}[{index}] lacks the encoded fields {missing}")
    return problems


def _rule_3(viz: Drawn) -> list[str]:
    problems = []
    for channel, rows in _category_channels(viz):
        domain = channel.domain
        if len(set(domain)) != len(domain):
            problems.append(f"3: the domain of '{channel.field}' has duplicates")
        if channel is not _x_channel(viz) and len(domain) > MAX_SERIES_VALUES:
            problems.append(f"3: series '{channel.field}' has {len(domain)} values, over {MAX_SERIES_VALUES}")
        outside = {v for row in rows if (v := _value(row, channel.field)) not in domain}
        if outside:
            problems.append(
                f"3: '{channel.field}' holds values outside its domain: {sorted(map(str, outside))[:5]}"
            )
    return problems


def _x_channel(viz: Drawn) -> CategoryChannel | None:
    return viz.encoding.x if isinstance(viz, BarChart) else None


def _rule_4(viz: Drawn) -> list[str]:
    problems = []
    for channel, rows in _quantitative_channels(viz):
        # A statistic of no trials has no value: only a row that cites no trial may leave it null.
        values = [
            _value(row, channel.field)
            for row in rows
            if not (
                channel.field != COUNT_FIELD
                and row.citation_count == 0
                and _value(row, channel.field) is None
            )
        ]
        if any(isinstance(v, bool) or not isinstance(v, int | float) for v in values):
            problems.append(f"4: '{channel.field}' holds a value that is not a number")
        elif channel.scale == "log" and any(v <= 0 for v in values if isinstance(v, int | float)):
            problems.append(f"4: '{channel.field}' has a log scale and a value that is not above zero")
    return problems


def _period_index(label: object, unit: str) -> int | None:
    match = _PERIOD[unit].match(label) if isinstance(label, str) else None
    if not match:
        return None
    year = int(match.group(1))
    if unit == "year":
        return year
    return year * (4 if unit == "quarter" else 12) + int(match.group(2)) - 1


def _rule_5_to_7(viz: Drawn) -> list[str]:
    """Periods (5), the (x, series) grid (6) and the order of bar rows (7)."""
    if not isinstance(viz, BarChart | TimeSeries):
        return []
    enc = viz.encoding
    rows = viz.data
    xs = [_value(row, enc.x.field) for row in rows]
    problems = []
    if isinstance(viz, TimeSeries):
        unit = viz.encoding.x.time_unit
        indexes = [_period_index(x, unit) for x in xs]
        if None in indexes:
            return [f"5: '{enc.x.field}' holds a value that is not a {unit} label"]
        ordered = [i for i in indexes if i is not None]
        if ordered != sorted(ordered):
            problems.append("5: periods do not ascend")
        distinct = sorted(set(ordered))
        if distinct and distinct[-1] - distinct[0] + 1 != len(distinct):
            problems.append("5: periods have gaps")
        grid_x = set(xs)
    else:
        domain = list(viz.encoding.x.domain)
        positions = [domain.index(x) for x in xs if x in domain]
        if positions != sorted(positions):
            problems.append("7: bar rows do not follow the order of x.domain")
        grid_x = set(domain)
    series = list(enc.series.domain) if enc.series else [None]
    pairs = [
        (x, _value(row, enc.series.field) if enc.series else None) for x, row in zip(xs, rows, strict=True)
    ]
    duplicates = [pair for pair, n in Counter(pairs).items() if n > 1]
    if duplicates:
        problems.append(f"6: (x, series) pairs repeat: {duplicates[:3]}")
    if set(pairs) != {(x, s) for x in grid_x for s in series}:
        problems.append("6: the (x, series) pairs are not the full grid")
    return problems


def _rule_8(viz: Drawn) -> list[str]:
    if not isinstance(viz, BarChart | TimeSeries) or viz.stack == "none":
        return []
    if isinstance(viz, TimeSeries) and viz.mark == "line":
        return ["8: a line is never stacked"]
    if viz.encoding.series is None or not viz.encoding.series.is_exclusive:
        return ["8: a stacked chart needs a series whose values are exclusive"]
    return []


def _rule_9(viz: Drawn) -> list[str]:
    if not isinstance(viz, Histogram):
        return []
    start, end = viz.encoding.x.field, viz.encoding.x2.field
    rows = viz.data
    problems = [
        f"9: bin {i} ends at {_value(a, end)} but the next starts at {_value(b, start)}"
        for i, (a, b) in enumerate(pairwise(rows))
        if _value(a, end) != _value(b, start)
    ]
    if any(_value(row, end) is None for row in rows[:-1]):
        problems.append("9: only the last bin may be open-ended")
    return problems


def _rule_10(viz: Drawn) -> list[str]:
    if not isinstance(viz, NetworkGraph):
        return []
    problems = []
    for label, rows in (("node", viz.data.nodes), ("link", viz.data.edges)):
        ids = [row.id for row in rows]
        if len(set(ids)) != len(ids):
            problems.append(f"10: {label} ids are not unique")
        reserved = sorted({key for row in rows for key in (row.model_extra or {})} & RESERVED_GRAPH_FIELDS)
        if reserved:
            problems.append(f"10: {label}s use reserved graph field names {reserved}")
    node_ids = {node.id for node in viz.data.nodes}
    dangling = [e.id for e in viz.data.edges if e.source not in node_ids or e.target not in node_ids]
    if dangling:
        problems.append(f"10: links reference missing nodes: {dangling[:3]}")
    return problems


# --- Rules 11 to 13: citations and counts ---------------------------------------------------------------


def _cited(row: Datum) -> set[str]:
    return {c.nct_id for c in row.citations}


def _rule_11(response: VisualizationResponse, rows: Sequence[Datum]) -> list[str]:
    problems = []
    maximum = response.meta.citations.max_per_datum
    for index, row in enumerate(rows):
        cited = _cited(row)
        if len(cited) > maximum:
            problems.append(f"11: row {index} cites {len(cited)} trials, over the maximum of {maximum}")
        if row.citation_count < len(cited):
            problems.append(
                f"11: row {index} citation_count {row.citation_count} is below its {len(cited)} citations"
            )
    cited_all = set().union(*(_cited(row) for row in rows))
    if cited_all != set(response.references):
        problems.append("11: references do not hold exactly the cited trials")
    if response.meta.citations.trials_cited != len(response.references):
        problems.append("11: meta.citations.trials_cited differs from the size of references")
    return problems


def _rule_12(response: VisualizationResponse) -> list[str]:
    counts = response.meta.counts
    if counts is None:
        return []
    return [
        f"12: series {s.label!r} matched {s.trials_matched} but analyzed {s.trials_analyzed} "
        f"and excluded {sum(e.count for e in s.trials_excluded)}"
        for s in counts.series
        if s.trials_matched != s.trials_analyzed + sum(e.count for e in s.trials_excluded)
    ]


def _count_field(viz: Drawn) -> str | None:
    """The field that holds trial counts, for the types whose rows count trials."""
    if isinstance(viz, BarChart | TimeSeries | Histogram):
        field = viz.encoding.y.field
    elif isinstance(viz, Metric):
        field = viz.encoding.value.field
    elif isinstance(viz, NetworkGraph):
        return COUNT_FIELD
    else:
        return None
    return field if field == COUNT_FIELD else None


def _rule_13(viz: Drawn, rows: Sequence[Datum]) -> list[str]:
    field = _count_field(viz)
    if field is None:
        return []
    return [
        f"13: row {i} draws {_value(row, field)} but cites {row.citation_count} trials"
        for i, row in enumerate(rows)
        if _value(row, field) != row.citation_count
    ]


# --- Rules 14 to 16: provenance, which the running request vouches for ---------------------------------


def _citations(response: VisualizationResponse, rows: Sequence[Datum]) -> Iterator[Citation]:
    for row in rows:
        yield from row.citations


def _rule_14_and_15(
    response: VisualizationResponse, rows: Sequence[Datum], provenance: Provenance
) -> list[str]:
    problems = []
    ids = set(response.references) | {c.nct_id for c in _citations(response, rows)}
    problems += [
        f"14: {nct_id} was not returned by ClinicalTrials.gov during this request"
        for nct_id in sorted(ids)
        if not provenance.was_returned(nct_id)
    ]
    for citation in _citations(response, rows):
        if (
            provenance.was_returned(citation.nct_id)
            and provenance.value_at(citation.nct_id, citation.field) != citation.excerpt
        ):
            problems.append(
                f"15: the excerpt of {citation.nct_id} at {citation.field} differs from the record"
            )
    for nct_id, reference in response.references.items():
        for evidence in reference.scope_evidence:
            if evidence.field is not None and provenance.value_at(nct_id, evidence.field) != evidence.excerpt:
                problems.append(
                    f"15: the scope evidence of {nct_id} at {evidence.field} differs from the record"
                )
    return problems


def _rule_16(viz: Drawn, provenance: Provenance) -> list[str]:
    """A count row whose `source_url` was requested equals that request's `totalCount`."""
    field = _count_field(viz)
    if field is None or isinstance(viz, NetworkGraph):
        return []
    problems = []
    for row in viz.data:
        if row.source_url is None:
            continue
        logged = provenance.total_count(row.source_url)
        if logged is not None and logged != _value(row, field):
            problems.append(
                f"16: a row draws {_value(row, field)} but its source_url returned totalCount {logged}"
            )
    return problems


# --- Rule 17: the partition checksum -------------------------------------------------------------------


def _rule_17(response: VisualizationResponse, viz: Drawn) -> list[str]:
    """Rows of one scope add up to its `trials_analyzed` when every dimension drawn is exclusive."""
    counts = response.meta.counts
    if counts is None or not isinstance(viz, BarChart | TimeSeries):
        return []
    enc = viz.encoding
    is_comparison = enc.series is not None and enc.series.field == GROUP_FIELD
    drawn: list[CategoryChannel] = [enc.series] if enc.series is not None and not is_comparison else []
    if isinstance(viz, BarChart):
        drawn.append(viz.encoding.x)
    if enc.y.field != COUNT_FIELD or not all(channel.is_exclusive for channel in drawn):
        return []
    totals: Counter[str | None] = Counter()
    for row in viz.data:
        group = _value(row, GROUP_FIELD) if is_comparison else None
        count = _value(row, COUNT_FIELD)
        totals[group if isinstance(group, str) else None] += count if isinstance(count, int) else 0
    analyzed = {s.label: s.trials_analyzed for s in counts.series}
    return [
        f"17: the rows of {group!r} add up to {total} but {analyzed.get(group)} trials were analyzed"
        for group, total in totals.items()
        if analyzed.get(group) != total
    ]
