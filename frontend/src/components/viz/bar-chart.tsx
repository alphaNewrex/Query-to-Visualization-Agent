"use client";

import * as React from "react";

import { pivot } from "@/lib/pivot";

import { BarsChart } from "./bars";
import { ChartFrame, markLabel, selection, useElementWidth } from "./chart-kit";
import type { RendererProps } from "./renderer-props";

/** Width of the longest category label, per character, plus room around it. */
const LABEL_CHAR = 6.6;
const LABEL_PADDING = 14;
const VALUE_AXIS_WIDTH = 56;

/**
 * Single, grouped and stacked bars, vertical or horizontal. `orientation` is a drawing hint
 * (PLAN 5.4): x is the category axis either way. A vertical chart whose labels would collide at
 * the width it has is drawn horizontally, so a phone shows readable labels instead of clipped ones.
 */
export function BarChartView({ spec, onSelect }: RendererProps<"bar_chart">) {
  const { x, y, series, tooltip } = spec.encoding;
  const pivoted = React.useMemo(
    () => pivot(spec.data, { x: x.field, y: y.field, xDomain: x.domain, series, valueLabel: y.title }),
    [spec.data, x.field, x.domain, y.field, y.title, series],
  );
  const [ref, width] = useElementWidth<HTMLDivElement>();

  const categories = pivoted.rows.length;
  const longest = pivoted.rows.reduce((max, row) => Math.max(max, row.x.length), 0);
  const isCrowded = width > 0 && longest * LABEL_CHAR + LABEL_PADDING > (width - VALUE_AXIS_WIDTH) / Math.max(1, categories);
  const horizontal = spec.orientation === "horizontal" || isCrowded;

  const stacked = spec.stack === "stacked";
  const barsPerCategory = stacked ? 1 : Math.max(1, pivoted.series.length);
  const height = horizontal ? Math.min(760, Math.max(220, categories * (barsPerCategory * 20 + 14) + 56)) : 340;

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
      <ChartFrame yTitle={y.title} xTitle={x.title} series={series ? pivoted.series : []} legendLabel={series?.title ?? "Series"}>
        <BarsChart
          pivoted={pivoted}
          y={y}
          extras={tooltip}
          stacked={stacked}
          horizontal={horizontal}
          tipLabels={!series && categories <= 24}
          height={height}
          onPick={onPick}
        />
      </ChartFrame>
    </div>
  );
}
