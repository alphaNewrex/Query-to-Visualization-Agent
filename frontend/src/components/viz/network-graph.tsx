"use client";

import * as React from "react";

import { formatWithUnit } from "@/lib/format";
import type { DatumSelection } from "@/lib/types";

import { SeriesLegend, seriesColor, useElementWidth } from "./chart-kit";
import { layoutGraph, type PlacedLink, type PlacedNode } from "./graph-layout";
import type { RendererProps } from "./renderer-props";

const FALLBACK_WIDTH = 880;
const MIN_WIDTH = 300;
const MAX_WIDTH = 1100;
const LABEL_SIZE = 11;
/** A link is picked up from this wide, whatever it is drawn at. */
const LINK_HIT_WIDTH = 14;

type Hover = { kind: "node"; id: string } | { kind: "link"; id: string } | null;

const clamp = (value: number, low: number, high: number): number => Math.min(high, Math.max(low, value));

/**
 * A network drawn as hand-written SVG over a d3-force layout. Node area encodes the size field and
 * link width the weight. A node, a link and the wide invisible stroke under each link are click
 * targets that report their datum. All third-party text goes in as text nodes, never as markup.
 */
export function NetworkGraphView({ spec, onSelect }: RendererProps<"network_graph">) {
  const { nodes: nodeEncoding, edges: edgeEncoding } = spec.encoding;
  const [ref, measured] = useElementWidth<HTMLDivElement>();
  // The layout runs at the width the chart is shown at, in steps, so that sizes and labels stay at about one pixel per unit.
  const width = Math.round(clamp(measured > 0 ? measured : FALLBACK_WIDTH, MIN_WIDTH, MAX_WIDTH) / 40) * 40;
  const height = clamp(Math.round(width * 0.62), 380, 620);
  const layout = React.useMemo(() => layoutGraph(spec, { width, height }), [spec, width, height]);
  const [hover, setHover] = React.useState<Hover>(null);

  const nodeValue = (node: PlacedNode): string =>
    node.value !== null && nodeEncoding.size ? formatWithUnit(node.value, nodeEncoding.size) : "";
  const linkValue = (link: PlacedLink): string =>
    link.weight !== null && edgeEncoding.weight ? formatWithUnit(link.weight, edgeEncoding.weight) : "";
  const linkName = (link: PlacedLink): string => `${link.source.label} – ${link.target.label}`;

  const pickNode = (node: PlacedNode) => onSelect(toSelection(node.label, nodeValue(node), node.datum));
  const pickLink = (link: PlacedLink) => onSelect(toSelection(linkName(link), linkValue(link), link.datum));

  // What stays at full strength while a mark is hovered: that mark and what it touches.
  const lit = React.useMemo(() => {
    if (hover === null) {
      return null;
    }
    const nodes = new Set<string>();
    const links = new Set<string>();
    if (hover.kind === "node") {
      nodes.add(hover.id);
      for (const link of layout.links) {
        if (link.source.id === hover.id || link.target.id === hover.id) {
          links.add(link.id);
          nodes.add(link.source.id);
          nodes.add(link.target.id);
        }
      }
    } else {
      const link = layout.links.find((candidate) => candidate.id === hover.id);
      if (link) {
        links.add(link.id);
        nodes.add(link.source.id);
        nodes.add(link.target.id);
      }
    }
    return { nodes, links };
  }, [hover, layout.links]);

  const hoveredNode = hover?.kind === "node" ? layout.nodes.find((node) => node.id === hover.id) : undefined;
  const hoveredLink = hover?.kind === "link" ? layout.links.find((link) => link.id === hover.id) : undefined;
  const readout = hoveredNode
    ? `${hoveredNode.label}${nodeValue(hoveredNode) ? ` · ${nodeValue(hoveredNode)}` : ""}`
    : hoveredLink
      ? `${linkName(hoveredLink)}${linkValue(hoveredLink) ? ` · ${linkValue(hoveredLink)}` : ""}`
      : null;

  const colorOf = (group: number): string => seriesColor(nodeEncoding.color ? group : 0);
  const { viewBox } = layout;

  if (layout.nodes.length === 0) {
    return <p className="text-sm text-muted-foreground">The graph has no nodes.</p>;
  }

  // The hovered node is drawn last, so that nothing covers it.
  const drawOrder = hoveredNode ? [...layout.nodes.filter((node) => node !== hoveredNode), hoveredNode] : layout.nodes;

  return (
    <div ref={ref} className="flex flex-col gap-2">
      {nodeEncoding.color ? (
        <SeriesLegend
          label={nodeEncoding.color.title}
          items={nodeEncoding.color.domain.map((value, index) => ({ label: value, color: seriesColor(index) }))}
        />
      ) : null}
      <svg
        role="group"
        aria-label={spec.title}
        viewBox={`${viewBox.x} ${viewBox.y} ${viewBox.width} ${viewBox.height}`}
        className="mx-auto block h-auto w-full"
        style={{ maxWidth: viewBox.width * 1.5, maxHeight: "72vh" }}
        onMouseLeave={() => setHover(null)}
      >
        <g>
          {layout.links.map((link) => {
            const dimmed = lit !== null && !lit.links.has(link.id);
            return (
              <g key={link.id} opacity={dimmed ? 0.12 : 1}>
                {/* A wide transparent stroke under the thin link is what the pointer lands on. */}
                <line
                  data-mark="link"
                  x1={link.source.x}
                  y1={link.source.y}
                  x2={link.target.x}
                  y2={link.target.y}
                  stroke="transparent"
                  strokeWidth={Math.max(LINK_HIT_WIDTH, link.width + 8)}
                  strokeLinecap="round"
                  className="cursor-pointer"
                  onClick={() => pickLink(link)}
                  onMouseEnter={() => setHover({ kind: "link", id: link.id })}
                >
                  <title>{`${linkName(link)}${linkValue(link) ? `: ${linkValue(link)}` : ""}`}</title>
                </line>
                <line
                  x1={link.source.x}
                  y1={link.source.y}
                  x2={link.target.x}
                  y2={link.target.y}
                  stroke="var(--muted-foreground)"
                  strokeOpacity={lit?.links.has(link.id) ? 0.85 : 0.4}
                  strokeWidth={link.width}
                  strokeLinecap="round"
                  pointerEvents="none"
                />
              </g>
            );
          })}
        </g>
        <g>
          {drawOrder.map((node) => {
            const dimmed = lit !== null && !lit.nodes.has(node.id);
            const pick = () => pickNode(node);
            return (
              <g
                key={node.id}
                role="button"
                tabIndex={0}
                aria-label={`${node.label}${nodeValue(node) ? `, ${nodeValue(node)}` : ""}: show the trials behind this node`}
                className="cursor-pointer outline-none"
                opacity={dimmed ? 0.2 : 1}
                onClick={pick}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    pick();
                  }
                }}
                onMouseEnter={() => setHover({ kind: "node", id: node.id })}
                onFocus={() => setHover({ kind: "node", id: node.id })}
                onBlur={() => setHover(null)}
              >
                <circle
                  data-mark="node"
                  cx={node.x}
                  cy={node.y}
                  r={node.r}
                  fill={colorOf(node.group)}
                  stroke="var(--card)"
                  strokeWidth={2}
                />
                <title>{`${node.label}${nodeValue(node) ? `: ${nodeValue(node)}` : ""}`}</title>
              </g>
            );
          })}
        </g>
        <g pointerEvents="none">
          {layout.nodes.map((node) => {
            const shown = layout.labelled.has(node.id) || hoveredNode === node;
            if (!shown) {
              return null;
            }
            const text = hoveredNode === node ? node.label : node.labelText;
            const place =
              node.side === "left"
                ? { x: node.x - node.r - 6, y: node.y, anchor: "end" as const, baseline: "central" as const }
                : node.side === "right"
                  ? { x: node.x + node.r + 6, y: node.y, anchor: "start" as const, baseline: "central" as const }
                  : { x: node.x, y: node.y + node.r + 12, anchor: "middle" as const, baseline: "auto" as const };
            return (
              <text
                key={node.id}
                x={place.x}
                y={place.y}
                textAnchor={place.anchor}
                dominantBaseline={place.baseline}
                fontSize={LABEL_SIZE}
                fill="var(--foreground)"
                stroke="var(--card)"
                strokeWidth={3}
                strokeLinejoin="round"
                paintOrder="stroke"
              >
                {text}
              </text>
            );
          })}
        </g>
      </svg>
      <div className="min-h-5 text-center text-xs text-muted-foreground" aria-live="polite">
        {readout ?? "Hover a node or a link for its value; click for the trials behind it."}
      </div>
      <div className="text-center text-xs text-muted-foreground">
        {nodeEncoding.size ? `Node area: ${nodeEncoding.size.title}. ` : ""}
        {edgeEncoding.weight ? `Link width: ${edgeEncoding.weight.title}.` : ""}
      </div>
    </div>
  );
}

function toSelection(label: string, value: string, datum: DatumSelection["datum"]): DatumSelection {
  return { label, value, datum };
}
