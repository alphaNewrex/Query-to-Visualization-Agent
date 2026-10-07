"use client";

import type * as React from "react";

import type { DatumSelection, Viz, VizType } from "@/lib/types";

import { BarChartView } from "./bar-chart";
import { HistogramView } from "./histogram";
import { MetricView } from "./metric";
import { NetworkGraphView } from "./network-graph";
import type { RendererProps } from "./renderer-props";
import { ScatterPlotView } from "./scatter-plot";
import { TableView } from "./table-view";
import { TimeSeriesView } from "./time-series";

/**
 * The type-to-renderer registry (PLAN 6.3). It is a mapped type over the generated union, so a
 * visualization type without a renderer does not compile.
 */
export const RENDERERS: { [K in VizType]: React.ComponentType<RendererProps<K>> } = {
  bar_chart: BarChartView,
  time_series: TimeSeriesView,
  histogram: HistogramView,
  scatter_plot: ScatterPlotView,
  network_graph: NetworkGraphView,
  table: TableView,
  metric: MetricView,
};

export function VisualizationView({
  spec,
  onSelect,
}: {
  spec: Viz;
  onSelect: (selection: DatumSelection) => void;
}) {
  // Each entry of the registry is typed for its own slice of the union; here the slice is known only at run time.
  const Renderer = RENDERERS[spec.type] as React.ComponentType<{ spec: Viz; onSelect: (selection: DatumSelection) => void }>;
  return <Renderer spec={spec} onSelect={onSelect} />;
}
