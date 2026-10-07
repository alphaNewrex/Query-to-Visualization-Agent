import { describe, expect, it } from "vitest";

import { fittingTicks, wrapLabel } from "@/components/viz/chart-kit";
import { layoutGraph } from "@/components/viz/graph-layout";
import type { VizOf } from "@/lib/types";

import { network } from "./fixtures/responses";

function networkSpec(): VizOf<"network_graph"> {
  if (network.kind !== "visualization" || network.visualization.type !== "network_graph") {
    throw new Error("The network fixture is not a network graph.");
  }
  return network.visualization;
}

describe("wrapLabel", () => {
  it("keeps a label that fits on one line", () => {
    expect(wrapLabel("Early Phase 1", 14)).toEqual(["Early Phase 1"]);
  });

  it("breaks after a slash first", () => {
    expect(wrapLabel("Phase 1/Phase 2", 12)).toEqual(["Phase 1/", "Phase 2"]);
  });

  it("then at spaces", () => {
    expect(wrapLabel("No phase listed", 10)).toEqual(["No phase", "listed"]);
  });

  it("leaves a word that is too long on a line of its own", () => {
    expect(wrapLabel("Not Applicable", 6)).toEqual(["Not", "Applicable"]);
  });
});

describe("fittingTicks", () => {
  const years = Array.from({ length: 19 }, (_, index) => String(2008 + index));

  it("names every period when there is room, so that no label next to the last one is dropped", () => {
    expect(fittingTicks(years, 900)).toEqual(years);
  });

  it("otherwise names every n-th period counted back from the last, which is always named", () => {
    expect(fittingTicks(years, 290)).toEqual(["2008", "2011", "2014", "2017", "2020", "2023", "2026"]);
    // Twelve periods: every second one, ending on the last, so the first is not named.
    expect(fittingTicks(years.slice(7), 290)).toEqual(["2016", "2018", "2020", "2022", "2024", "2026"]);
  });

  it("leaves the choice to the chart while the width is not known", () => {
    expect(fittingTicks(years, 0)).toBeNull();
    expect(fittingTicks([], 500)).toBeNull();
  });

  it("allows for longer labels", () => {
    const quarters = Array.from({ length: 12 }, (_, index) => `${2023 + Math.floor(index / 4)}-Q${(index % 4) + 1}`);
    const ticks = fittingTicks(quarters, 400) ?? [];
    expect(ticks.length).toBeLessThan(quarters.length);
    expect(ticks[ticks.length - 1]).toBe("2025-Q4");
  });
});

describe("layoutGraph", () => {
  const size = { width: 880, height: 545 };

  it("gives the same layout for the same graph and size", () => {
    const spec = networkSpec();
    expect(layoutGraph(spec, size)).toEqual(layoutGraph(spec, size));
  });

  it("leaves the specification as it was: d3-force writes into what it is given", () => {
    const spec = networkSpec();
    const before = JSON.stringify(spec);
    layoutGraph(spec, size);
    layoutGraph({ ...spec, layout: "force" }, size);
    expect(JSON.stringify(spec)).toBe(before);
  });

  it("puts the two sides of a bipartite graph in two columns, one side left of the other", () => {
    const layout = layoutGraph(networkSpec(), size);
    const left = layout.nodes.filter((node) => node.group === 0);
    const right = layout.nodes.filter((node) => node.group === 1);
    expect(left.length + right.length).toBe(layout.nodes.length);
    expect(Math.max(...left.map((node) => node.x))).toBeLessThan(Math.min(...right.map((node) => node.x)));
    expect(new Set(left.map((node) => node.x)).size).toBe(1);

    // Nodes in a column never overlap.
    for (const column of [left, right]) {
      const sorted = [...column].sort((a, b) => a.y - b.y);
      sorted.slice(1).forEach((node, index) => expect(node.y - sorted[index].y).toBeGreaterThanOrEqual(node.r + sorted[index].r));
    }
  });

  it.each(["bipartite", "force"] as const)("fits every node into the view box (%s)", (mode) => {
    const layout = layoutGraph({ ...networkSpec(), layout: mode }, size);
    const { x, y, width, height } = layout.viewBox;
    for (const node of layout.nodes) {
      expect(node.x - node.r).toBeGreaterThanOrEqual(x);
      expect(node.x + node.r).toBeLessThanOrEqual(x + width);
      expect(node.y - node.r).toBeGreaterThanOrEqual(y);
      expect(node.y + node.r).toBeLessThanOrEqual(y + height);
    }
  });

  it("draws area by value: a node with four times the trials has twice the radius", () => {
    const spec = networkSpec();
    const sized = spec.data.nodes.map((node) => ({ ...node, trial_count: node.id === spec.data.nodes[0].id ? 16 : 4 }));
    const layout = layoutGraph({ ...spec, data: { ...spec.data, nodes: sized } }, size);
    const [first, second] = layout.nodes;
    expect(first.r / second.r).toBeCloseTo(2, 5);
  });

  it("skips a link whose end is not a node", () => {
    const spec = networkSpec();
    const edges = [...spec.data.edges, { ...spec.data.edges[0], id: "dangling", target: "drug:does-not-exist" }];
    const layout = layoutGraph({ ...spec, data: { ...spec.data, edges } }, size);
    expect(layout.links).toHaveLength(spec.data.edges.length);
  });

  it("labels the largest nodes and no more than the limit", () => {
    const layout = layoutGraph({ ...networkSpec(), layout: "force" }, size);
    expect(layout.labelled.size).toBeLessThanOrEqual(15);
    const largest = [...layout.nodes].sort((a, b) => (b.value ?? 0) - (a.value ?? 0))[0];
    expect(layout.labelled.has(largest.id)).toBe(true);
  });
});
