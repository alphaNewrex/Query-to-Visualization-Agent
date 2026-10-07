// Renders every committed example (docs/examples/*/response.json, written into
// fixtures/examples.gen.ts by `node scripts/gen-types.mjs --fixtures`): the backend proves it
// produces these responses, and this file proves that the page draws them (PLAN 6.7).
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ResultView } from "@/components/result-view";
import { cellNumber, cellText } from "@/lib/cell";
import type { Datum, DatumSelection, QueryResponse, Viz, VizOf } from "@/lib/types";

import { examples } from "./fixtures/examples.gen";

afterEach(cleanup);

const entries = Object.entries(examples);

/** The index of the mark to click: never the first, where there are several. */
const third = (count: number) => Math.min(2, count - 1);

function show(response: QueryResponse, onSelect: (selection: DatumSelection) => void = () => undefined) {
  return render(<ResultView response={response} request={null} onSelect={onSelect} onRun={() => undefined} />);
}

function escaped(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** The visualization of a response, narrowed to its type. */
function visualization<T extends Viz["type"]>(response: QueryResponse, type: T): VizOf<T> {
  if (response.kind !== "visualization" || response.visualization.type !== type) {
    throw new Error(`The response is not a ${type}.`);
  }
  return response.visualization as VizOf<T>;
}

/** What a selection must carry: the datum of the clicked mark, with the citations the backend sent for it. */
function expectOwnDatum(onSelect: ReturnType<typeof vi.fn>, expected: Datum) {
  expect(onSelect).toHaveBeenCalledOnce();
  const { datum } = onSelect.mock.calls[0][0] as DatumSelection;
  expect(datum).toEqual(expected);
  expect(datum.citations.length).toBeGreaterThan(0);
  // A citation names a trial and a field: a point that is one trial can cite several fields of it.
  expect(datum.citation_count).toBeGreaterThanOrEqual(new Set(datum.citations.map((citation) => citation.nct_id)).size);
}

describe("the committed examples", () => {
  it("cover the seven chart types and a clarification", () => {
    const kinds = entries.map(([, response]) => (response.kind === "visualization" ? response.visualization.type : response.kind));
    for (const kind of ["time_series", "bar_chart", "network_graph", "histogram", "scatter_plot", "table", "metric", "clarification"]) {
      expect(kinds).toContain(kind);
    }
  });

  it.each(entries)("%s renders without falling back", (_name, response) => {
    const { container } = show(response);
    expect(screen.getByText(response.message)).toBeTruthy();
    expect(container.textContent).not.toContain("could not be drawn");
    if (response.kind === "visualization") {
      expect(screen.getByRole("heading", { name: response.visualization.title })).toBeTruthy();
      expect(screen.getByRole("tab", { name: "Chart" })).toBeTruthy();
    } else {
      expect(container.querySelector(`[data-outcome="${response.kind}"]`)).not.toBeNull();
    }
  });
});

describe("every mark of the committed examples", () => {
  const ofType = (type: string) => entries.filter(([, response]) => response.kind === "visualization" && response.visualization.type === type);

  it.each(ofType("time_series"))("%s: one point per row, and a click reports that row", async (_name, response) => {
    const spec = visualization(response, "time_series");
    const onSelect = vi.fn();
    const { container } = show(response, onSelect);
    expect(container.querySelectorAll(".recharts-line")).toHaveLength(spec.encoding.series ? spec.encoding.series.domain.length : 1);
    const points = screen.getAllByRole("button", { name: /show the trials behind this point/ });
    expect(points).toHaveLength(spec.data.length);
    const index = third(points.length);
    await userEvent.click(points[index]);
    expectOwnDatum(onSelect, spec.data[index]);
  });

  it.each(ofType("bar_chart"))("%s: one bar per row, and a click reports that row", async (_name, response) => {
    const spec = visualization(response, "bar_chart");
    const { x, y, series } = spec.encoding;
    const onSelect = vi.fn();
    const { container } = show(response, onSelect);
    const groups = [...container.querySelectorAll(".recharts-bar")];
    expect(groups).toHaveLength(series ? series.domain.length : 1);
    // A bar of height 0 is not drawn, so rows with no trials have none.
    expect(container.querySelectorAll(".recharts-bar-rectangle")).toHaveLength(spec.data.filter((row) => (cellNumber(row, y.field) ?? 0) > 0).length);

    // The last series, at the third category that has a bar: for a single series, simply the third bar.
    const group = groups[groups.length - 1];
    const bars = [...group.querySelectorAll(".recharts-bar-rectangle")];
    const index = third(bars.length);
    const seriesValue = series ? series.domain[groups.length - 1] : null;
    const drawn = spec.data.filter(
      (row) => (cellNumber(row, y.field) ?? 0) > 0 && (seriesValue === null || cellText(row, series?.field ?? "") === seriesValue),
    );
    await userEvent.click(bars[index].querySelector("path, rect") ?? bars[index]);
    expectOwnDatum(onSelect, drawn[index]);
    expect(cellText(drawn[index], x.field)).not.toBe("");
  });

  it.each(ofType("histogram"))("%s: one bar per bin, and a click reports that bin", async (_name, response) => {
    const spec = visualization(response, "histogram");
    const onSelect = vi.fn();
    const { container } = show(response, onSelect);
    const drawn = spec.data.filter((row) => (cellNumber(row, spec.encoding.y.field) ?? 0) > 0);
    const bars = container.querySelectorAll(".recharts-bar-rectangle");
    expect(bars).toHaveLength(drawn.length);
    const index = third(bars.length);
    await userEvent.click(bars[index].querySelector("path, rect") ?? bars[index]);
    expectOwnDatum(onSelect, drawn[index]);
  });

  it.each(ofType("scatter_plot"))("%s: one point per trial, and a click reports that trial", async (_name, response) => {
    const spec = visualization(response, "scatter_plot");
    const { x, y } = spec.encoding;
    const onSelect = vi.fn();
    const { container } = show(response, onSelect);
    // Both coordinates are needed to place a point; a log axis cannot place zero.
    const placeable = (value: number | null, log: boolean) => value !== null && (!log || value > 0);
    const drawn = spec.data.filter(
      (row) => placeable(cellNumber(row, x.field), x.scale === "log") && placeable(cellNumber(row, y.field), y.scale === "log"),
    );
    const symbols = container.querySelectorAll(".recharts-scatter-symbol");
    expect(symbols).toHaveLength(drawn.length);
    const index = third(symbols.length);
    await userEvent.click(symbols[index].querySelector("path, circle") ?? symbols[index]);
    expectOwnDatum(onSelect, drawn[index]);
  });

  it.each(ofType("network_graph"))("%s: a circle per node and a line per link, and a click reports that node", async (_name, response) => {
    const spec = visualization(response, "network_graph");
    const onSelect = vi.fn();
    const { container } = show(response, onSelect);
    expect(container.querySelectorAll('[data-mark="node"]')).toHaveLength(spec.data.nodes.length);
    expect(container.querySelectorAll('[data-mark="link"]')).toHaveLength(spec.data.edges.length);

    const node = spec.data.nodes[third(spec.data.nodes.length)];
    const label = cellText(node, spec.encoding.nodes.label.field);
    await userEvent.click(screen.getByRole("button", { name: new RegExp(`^${escaped(label)}(,|:)`) }));
    expectOwnDatum(onSelect, node);
  });

  it.each(ofType("table"))("%s: one row per trial, and a click reports that trial", async (_name, response) => {
    const spec = visualization(response, "table");
    const onSelect = vi.fn();
    const { container } = show(response, onSelect);
    const rows = container.querySelectorAll('[data-mark="row"]');
    expect(rows).toHaveLength(spec.data.length);
    const index = third(rows.length);
    await userEvent.click(rows[index]);
    expectOwnDatum(onSelect, spec.data[index]);
  });

  it.each(ofType("metric"))("%s: the one number, and its button reports the one row", async (_name, response) => {
    const spec = visualization(response, "metric");
    const onSelect = vi.fn();
    const { container } = show(response, onSelect);
    expect(container.querySelectorAll('[data-mark="metric"]')).toHaveLength(1);
    expect(spec.data).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: /Show the trials/ }));
    expectOwnDatum(onSelect, spec.data[0]);
  });
});

describe("the Data tab of the committed examples", () => {
  it.each(entries.filter(([, response]) => response.kind === "visualization"))("%s: its rows are reachable by keyboard through a Citations button", async (_name, response) => {
    show(response);
    await userEvent.click(screen.getByRole("tab", { name: "Data" }));
    expect(screen.getAllByRole("button", { name: /^Citations for / }).length).toBeGreaterThan(0);
  });
});

describe("a recorded answer the page cannot draw", () => {
  const barChart = () => structuredClone(examples["03-recruiting-by-country"]);

  it("falls back to its message, its rows and its JSON when the type is one this page does not know", () => {
    const response = barChart() as unknown as { visualization: { type: string } };
    response.visualization.type = "sunburst";
    const { container } = show(response as unknown as QueryResponse);
    expect(screen.getByText(/This answer is in a form this page cannot draw/)).toBeTruthy();
    expect(container.querySelectorAll("tbody tr").length).toBeGreaterThan(0);
    expect(screen.getByText("Raw JSON")).toBeTruthy();
  });

  it("is caught by the error boundary when the renderer throws, and the page stays up", () => {
    const quiet = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const response = barChart() as unknown as { visualization: { data: unknown } };
    response.visualization.data = null;
    const { container } = show(response as unknown as QueryResponse);
    quiet.mockRestore();
    expect(container.textContent).toContain("The chart could not be drawn");
    expect(screen.getByText("Raw JSON")).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Trace" })).toBeTruthy();
  });
});

