"use client";

import * as React from "react";

import { pivot } from "@/lib/pivot";

import { BarsChart } from "./bars";
import { ChartFrame, markLabel, selection, useElementWidth, wrapLabel } from "./chart-kit";
import type { RendererProps } from "./renderer-props";

/** Width of one character of the 12 px axis font, and the room a category label keeps around itself. */
const LABEL_CHAR = 6.6;
const LABEL_PADDING = 10;
/** The value axis and the chart's side margins. */
const VALUE_AXIS_WIDTH = 60;
const MAX_LABEL_LINES = 3;

/**
 * Single, grouped and stacked bars, vertical or horizontal. `orientation` is a drawing hint
 * (PLAN 5.4): x is the category axis either way. A vertical chart wraps its category labels onto
 * up to three lines; when even that would not keep them apart at the width it has, it is drawn
 * horizontally, so a phone shows readable labels instead of overprinted ones.
 */
export function BarChartView({ spec, onSelect }: RendererProps<"bar_chart">) {
  const { x, y, series, tooltip } = spec.encoding;
  const pivoted = React.useMemo(
    () => pivot(spec.data, { x: x.field, y: y.field, xDomain: x.domain, series, valueLabel: y.title }),
    [spec.data, x.field, x.domain, y.field, y.title, series],
  );
  const [ref, width] = useElementWidth<HTMLDivElement>();

  const categories = pivoted.rows.length;
  const labels = pivoted.rows.map((row) => row.x);
  // Characters per line that a vertical chart can give each label at this width; unknown until measured.
  const maxChars = width > 0 ? Math.floor(((width - VALUE_AXIS_WIDTH) / Math.max(1, categories) - LABEL_PADDING) / LABEL_CHAR) : null;
  const wrapped = maxChars === null ? null : labels.map((label) => wrapLabel(label, maxChars));
  const lineCount = wrapped === null ? 1 : wrapped.reduce((max, lines) => Math.max(max, lines.length), 1);
  // A line is longer than `maxChars` only when it is one word that cannot be broken.
  const longestLine =
    wrapped === null ? 0 : wrapped.reduce((max, lines) => lines.reduce((inner, line) => Math.max(inner, line.length), max), 0);
  const isCrowded = maxChars !== null && (lineCount > MAX_LABEL_LINES || longestLine > maxChars + 1 || maxChars < 5);
  const horizontal = spec.orientation === "horizontal" || isCrowded;

  const stacked = spec.stack === "stacked";
  const barsPerCategory = stacked ? 1 : Math.max(1, pivoted.series.length);
  const height = horizontal ? Math.min(760, Math.max(220, categories * (barsPerCategory * 20 + 14) + 56)) : 340 + (lineCount - 1) * 14;

  const onPick = (xLabel: string, seriesKey: string) => {
    const datum = pivoted.find(xLabel, seriesKey);
    if (datum === undefined) {
      return;
    }
    const entry = pivoted.series.find((candidate) => candidate.key === seriesKey);
    onSelect(selection(markLabel(xLabel, series && entry ? entry.label : null), datum, y));
  };

  return (
    <div ref={ref}>
      {/* The captions follow the axes: with horizontal bars the categories run down the left and the values along the bottom. */}
      <ChartFrame
        yTitle={horizontal ? x.title : y.title}
        xTitle={horizontal ? y.title : x.title}
        series={series ? pivoted.series : []}
        legendLabel={series?.title ?? "Series"}
      >
        <BarsChart
          pivoted={pivoted}
          y={y}
          extras={tooltip}
          stacked={stacked}
          horizontal={horizontal}
          tipLabels={!series && categories <= 24}
          categoryLabels={!horizontal && maxChars !== null ? { maxChars, maxLines: Math.min(lineCount, MAX_LABEL_LINES) } : undefined}
          height={height}
          onPick={onPick}
        />
      </ChartFrame>
    </div>
  );
}
