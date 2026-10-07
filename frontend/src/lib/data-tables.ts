/**
 * The Data tab: every visualization's rows as tables, built from its encoding. It exists to show
 * that the rows are tidy, so nothing here sorts, filters or computes; it only names columns.
 */
import { cellText } from "./cell";
import { formatDatum } from "./format";
import type {
  CategoryChannel,
  Datum,
  FieldDef,
  QuantitativeChannel,
  TemporalChannel,
  Viz,
} from "./types";

export interface DataTableSpec {
  title: string | null;
  columns: FieldDef[];
  rows: Datum[];
  /** The column whose value is shown beside a row's name in the citation sheet. */
  measure: FieldDef | null;
}

function def(field: string, title: string, type: FieldDef["type"], extra: Partial<FieldDef> = {}): FieldDef {
  return { field, title, type, unit: null, format: null, href_field: null, ...extra };
}

const ofQuantitative = (channel: QuantitativeChannel): FieldDef =>
  def(channel.field, channel.title, "quantitative", { unit: channel.unit, format: channel.format });
const ofCategory = (channel: CategoryChannel): FieldDef => def(channel.field, channel.title, channel.type);
const ofTemporal = (channel: TemporalChannel): FieldDef => def(channel.field, channel.title, "temporal");

/** Columns in order, without a second column for a field already shown. */
function unique(columns: readonly (FieldDef | null)[]): FieldDef[] {
  const seen = new Set<string>();
  const kept: FieldDef[] = [];
  for (const column of columns) {
    if (column !== null && !seen.has(column.field)) {
      seen.add(column.field);
      kept.push(column);
    }
  }
  return kept;
}

export function dataTablesFor(visualization: Viz): DataTableSpec[] {
  switch (visualization.type) {
    case "bar_chart": {
      const { x, y, series, tooltip } = visualization.encoding;
      return [
        {
          title: null,
          columns: unique([ofCategory(x), series && ofCategory(series), ofQuantitative(y), ...tooltip]),
          rows: visualization.data,
          measure: ofQuantitative(y),
        },
      ];
    }
    case "time_series": {
      const { x, y, series, tooltip } = visualization.encoding;
      return [
        {
          title: null,
          columns: unique([ofTemporal(x), series && ofCategory(series), ofQuantitative(y), ...tooltip]),
          rows: visualization.data,
          measure: ofQuantitative(y),
        },
      ];
    }
    case "histogram": {
      const { x, x2, y, label, tooltip } = visualization.encoding;
      return [
        {
          title: null,
          columns: unique([
            def(label.field, "Bin", "ordinal"),
            def(x.field, `${x.title} (from)`, "quantitative", { unit: x.unit, format: x.format }),
            def(x2.field, `${x.title} (to, exclusive)`, "quantitative", { unit: x.unit, format: x.format }),
            ofQuantitative(y),
            ...tooltip,
          ]),
          rows: visualization.data,
          measure: ofQuantitative(y),
        },
      ];
    }
    case "scatter_plot": {
      const { x, y, series, size, label, tooltip } = visualization.encoding;
      return [
        {
          title: null,
          columns: unique([
            label,
            series && ofCategory(series),
            ofQuantitative(x),
            ofQuantitative(y),
            size && ofQuantitative(size),
            ...tooltip,
          ]),
          rows: visualization.data,
          measure: ofQuantitative(y),
        },
      ];
    }
    case "table":
      return [
        {
          title: null,
          columns: visualization.encoding.columns,
          rows: visualization.data,
          measure: visualization.encoding.columns.find((column) => column.type === "quantitative") ?? null,
        },
      ];
    case "metric":
      return [
        {
          title: null,
          columns: [ofQuantitative(visualization.encoding.value)],
          rows: visualization.data,
          measure: ofQuantitative(visualization.encoding.value),
        },
      ];
    case "network_graph": {
      const { nodes, edges } = visualization.encoding;
      const labels = new Map(visualization.data.nodes.map((node) => [node.id, cellText(node, nodes.label.field) || node.id]));
      return [
        {
          title: "Nodes",
          columns: unique([
            def(nodes.label.field, "Node", "nominal"),
            nodes.color && ofCategory(nodes.color),
            nodes.size && ofQuantitative(nodes.size),
            ...nodes.tooltip,
          ]),
          rows: visualization.data.nodes,
          measure: nodes.size && ofQuantitative(nodes.size),
        },
        {
          title: "Links",
          columns: unique([
            def("source", "From", "nominal"),
            def("target", "To", "nominal"),
            edges.weight && ofQuantitative(edges.weight),
            ...edges.tooltip,
          ]),
          // A link names its ends by node id; the table shows their labels.
          rows: visualization.data.edges.map((edge) => ({
            ...edge,
            source: labels.get(edge.source) ?? edge.source,
            target: labels.get(edge.target) ?? edge.target,
          })),
          measure: edges.weight && ofQuantitative(edges.weight),
        },
      ];
    }
  }
}

/** A row's name: its first two non-numeric cells, joined. */
export function rowName(columns: readonly FieldDef[], row: Datum, index: number): string {
  const parts = columns
    .filter((column) => column.type !== "quantitative")
    .slice(0, 2)
    .map((column) => cellText(row, column.field))
    .filter((text) => text !== "");
  return parts.length > 0 ? parts.join(" · ") : `Row ${index + 1}`;
}

export function rowValue(measure: FieldDef | null, row: Datum): string {
  return measure === null ? "" : `${measure.title} ${formatDatum(row, measure)}`;
}
