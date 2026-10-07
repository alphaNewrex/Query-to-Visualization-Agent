/**
 * Node and link positions for the network graph (PLAN 6.3). Pure: the same spec and size always
 * give the same layout, and the spec is never mutated, because d3-force writes positions into the
 * objects it is given and replaces link endpoints with node objects. Everything it simulates is a
 * copy made here.
 */
import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY } from "d3-force";
import type { SimulationLinkDatum, SimulationNodeDatum } from "d3-force";

import { cellNumber, cellText } from "@/lib/cell";
import type { NetworkEdge, NetworkNode, VizOf } from "@/lib/types";

type Spec = VizOf<"network_graph">;

export interface PlacedNode {
  id: string;
  datum: NetworkNode;
  label: string;
  /** The label cut to what fits beside the node. */
  labelText: string;
  x: number;
  y: number;
  r: number;
  /** Index into the node colour domain; 0 or 1 in a bipartite graph. */
  group: number;
  value: number | null;
  /** Where the label sits: left or right of the node in a bipartite graph, below it otherwise. */
  side: "left" | "right" | "below";
}

export interface PlacedLink {
  id: string;
  datum: NetworkEdge;
  source: PlacedNode;
  target: PlacedNode;
  /** Stroke width in graph units. */
  width: number;
  weight: number | null;
}

export interface GraphLayout {
  nodes: PlacedNode[];
  links: PlacedLink[];
  /** Ids of the nodes that carry a label without being hovered. */
  labelled: ReadonlySet<string>;
  viewBox: { x: number; y: number; width: number; height: number };
}

interface SimNode extends SimulationNodeDatum {
  id: string;
  group: number;
  r: number;
}

const TICKS = 300;
const MIN_RADIUS = 4;
const PADDING = 16;
const LABEL_CHAR = 5.6;
const LABEL_GAP = 7;
/** The 15 largest nodes are labelled; a bipartite graph gives every label a column to itself, so it labels more. */
const FORCE_LABELS = 15;
const BIPARTITE_LABELS = 30;
const MIN_STROKE = 1;
const MAX_STROKE = 7;

const clamp = (value: number, low: number, high: number): number => Math.min(high, Math.max(low, value));

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, Math.max(1, max - 1))}…` : text;
}

/** Keeps a column of nodes from overlapping while preserving the order the simulation gave it. */
function spreadColumn(column: SimNode[], gap: number): void {
  if (column.length < 2) {
    return;
  }
  const sorted = [...column].sort((a, b) => (a.y ?? 0) - (b.y ?? 0));
  const meanBefore = sorted.reduce((sum, node) => sum + (node.y ?? 0), 0) / sorted.length;
  for (let index = 1; index < sorted.length; index += 1) {
    const previous = sorted[index - 1];
    const node = sorted[index];
    node.y = Math.max(node.y ?? 0, (previous.y ?? 0) + previous.r + node.r + gap);
  }
  const meanAfter = sorted.reduce((sum, node) => sum + (node.y ?? 0), 0) / sorted.length;
  for (const node of sorted) {
    node.y = (node.y ?? 0) + (meanBefore - meanAfter);
  }
}

export function layoutGraph(spec: Spec, size: { width: number; height: number }): GraphLayout {
  const { width, height } = size;
  const { nodes: nodeEncoding, edges: edgeEncoding } = spec.encoding;
  const isBipartite = spec.layout === "bipartite";
  const sizeField = nodeEncoding.size?.field ?? null;
  const weightField = edgeEncoding.weight?.field ?? null;
  const colorChannel = nodeEncoding.color;

  const values = spec.data.nodes.map((node) => (sizeField === null ? null : cellNumber(node, sizeField)));
  const largest = values.reduce<number>((max, value) => Math.max(max, value ?? 0), 0);
  const maxRadius = clamp(Math.sqrt((width * height) / Math.max(1, spec.data.nodes.length)) * 0.32, 9, 24);
  // Area encodes the value, so the radius follows its square root.
  const radiusOf = (value: number | null): number =>
    value === null || largest <= 0
      ? Math.max(MIN_RADIUS, maxRadius * 0.5)
      : Math.max(MIN_RADIUS, maxRadius * Math.sqrt(Math.max(value, 0) / largest));

  const maxChars = isBipartite ? clamp(Math.floor((width - 170) / 2 / LABEL_CHAR), 8, 26) : 22;
  const labelWidth = maxChars * LABEL_CHAR;

  const simNodes: SimNode[] = spec.data.nodes.map((node, index) => {
    const domainIndex = colorChannel ? colorChannel.domain.indexOf(cellText(node, colorChannel.field)) : 0;
    return {
      id: node.id,
      group: isBipartite ? clamp(domainIndex, 0, 1) : Math.max(0, domainIndex),
      r: radiusOf(values[index]),
    };
  });
  const byId = new Map(simNodes.map((node) => [node.id, node]));
  const edges = spec.data.edges.filter((edge) => byId.has(edge.source) && byId.has(edge.target));
  const simLinks: SimulationLinkDatum<SimNode>[] = edges.map((edge) => ({ source: edge.source, target: edge.target }));

  const halfW = (width - 2 * PADDING) / 2;
  const halfH = (height - 2 * PADDING) / 2;
  const columnX = Math.max(40, halfW - labelWidth - maxRadius - LABEL_GAP - 8);

  const simulation = forceSimulation<SimNode>(simNodes).stop();
  if (isBipartite) {
    simulation
      .force("x", forceX<SimNode>((node) => (node.group === 0 ? -columnX : columnX)).strength(1))
      .force("y", forceY<SimNode>(0).strength(0.06))
      .force("charge", forceManyBody<SimNode>().strength(-20))
      .force("collide", forceCollide<SimNode>((node) => node.r + 3))
      .force("link", forceLink<SimNode, SimulationLinkDatum<SimNode>>(simLinks).id((node) => node.id).strength(0.03));
  } else {
    // The pull towards the centre is weaker along x than along y, so that the graph spreads across the width it is shown at.
    simulation
      .force("charge", forceManyBody<SimNode>().strength(-170).distanceMax(500))
      .force("link", forceLink<SimNode, SimulationLinkDatum<SimNode>>(simLinks).id((node) => node.id).distance(80).strength(0.25))
      .force("center", forceCenter(0, 0))
      .force("collide", forceCollide<SimNode>((node) => node.r + 6))
      .force("x", forceX<SimNode>(0).strength(0.03))
      .force("y", forceY<SimNode>(0).strength(0.07));
  }
  for (let tick = 0; tick < TICKS; tick += 1) {
    simulation.tick();
    for (const node of simNodes) {
      node.x = isBipartite ? (node.group === 0 ? -columnX : columnX) : clamp(node.x ?? 0, -halfW + node.r, halfW - node.r);
      if (!isBipartite) {
        node.y = clamp(node.y ?? 0, -halfH + node.r, halfH - node.r);
      }
    }
  }
  if (isBipartite) {
    spreadColumn(simNodes.filter((node) => node.group === 0), 5);
    spreadColumn(simNodes.filter((node) => node.group !== 0), 5);
  }

  const nodes: PlacedNode[] = spec.data.nodes.map((datum, index) => {
    const sim = simNodes[index];
    const label = cellText(datum, nodeEncoding.label.field) || datum.id;
    return {
      id: datum.id,
      datum,
      label,
      labelText: truncate(label, maxChars),
      x: sim.x ?? 0,
      y: sim.y ?? 0,
      r: sim.r,
      group: sim.group,
      value: values[index],
      side: isBipartite ? (sim.group === 0 ? "left" : "right") : "below",
    };
  });
  const placedById = new Map(nodes.map((node) => [node.id, node]));

  const weights = edges.map((edge) => (weightField === null ? null : cellNumber(edge, weightField)));
  const heaviest = weights.reduce<number>((max, weight) => Math.max(max, weight ?? 0), 0);
  const links: PlacedLink[] = edges.flatMap((datum, index) => {
    const source = placedById.get(datum.source);
    const target = placedById.get(datum.target);
    if (source === undefined || target === undefined) {
      return [];
    }
    const weight = weights[index];
    const width =
      weight === null || heaviest <= 0
        ? 1.5
        : MIN_STROKE + (MAX_STROKE - MIN_STROKE) * (Math.max(weight, 0) / heaviest);
    return [{ id: datum.id, datum, source, target, width, weight }];
  });

  const limit = isBipartite ? BIPARTITE_LABELS : FORCE_LABELS;
  const labelled = new Set(
    nodes
      .map((node, index) => ({ node, index }))
      .sort((a, b) => (b.node.value ?? 0) - (a.node.value ?? 0) || a.index - b.index)
      .slice(0, limit)
      .map(({ node }) => node.id),
  );

  // The view box holds every node, and the labels that sit beside or under them.
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (const node of nodes) {
    const reach = labelled.has(node.id) ? node.labelText.length * LABEL_CHAR + LABEL_GAP : 0;
    const left = node.side === "left" ? reach : node.side === "below" ? reach / 2 : 0;
    const right = node.side === "right" ? reach : node.side === "below" ? reach / 2 : 0;
    const below = node.side === "below" && labelled.has(node.id) ? 18 : 0;
    minX = Math.min(minX, node.x - node.r - left);
    maxX = Math.max(maxX, node.x + node.r + right);
    minY = Math.min(minY, node.y - node.r);
    maxY = Math.max(maxY, node.y + node.r + below);
  }
  if (nodes.length === 0) {
    minX = minY = -PADDING;
    maxX = maxY = PADDING;
  }

  return {
    nodes,
    links,
    labelled,
    viewBox: {
      x: minX - PADDING,
      y: minY - PADDING,
      width: maxX - minX + 2 * PADDING,
      height: maxY - minY + 2 * PADDING,
    },
  };
}
