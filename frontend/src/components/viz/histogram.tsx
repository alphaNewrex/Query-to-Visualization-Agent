"use client";

import * as React from "react";

import { pivot } from "@/lib/pivot";

import { BarsChart } from "./bars";
import { ChartFrame, selection, useElementWidth } from "./chart-kit";
import type { RendererProps } from "./renderer-props";

/** Width of one character of the 12 px axis font, and what the chart keeps beside its bars: the value axis and the margins. */
const LABEL_CHAR = 6.6;
const BESIDE_BARS = 60;

/**
 * One bar per row, labelled with the row's own `bin_label` (PLAN 5.4): bins may be uneven and the
 * last may be open-ended, so the renderer never computes an axis from `bin_start` and `bin_end`.
 * Bin labels such as "1000-4999" cannot be wrapped, so where they would run into each other
 * (a phone) they are slanted instead.
 */
export function HistogramView({ spec, onSelect }: RendererProps<"histogram">) {
  const { x, y, label, tooltip } = spec.encoding;
  const pivoted = React.useMemo(
    () => pivot(spec.data, { x: label.field, y: y.field, valueLabel: y.title }),
    [spec.data, label.field, y.field, y.title],
  );
  const [ref, width] = useElementWidth<HTMLDivElement>();
  const longest = pivoted.rows.reduce((max, row) => Math.max(max, row.x.length), 0);
  const isCrowded = width > 0 && longest * LABEL_CHAR + 8 > (width - BESIDE_BARS) / Math.max(1, pivoted.rows.length);

  const onPick = (binLabel: string, seriesKey: string) => {
    const datum = pivoted.find(binLabel, seriesKey);
    if (datum !== undefined) {
      onSelect(selection(`${x.title}: ${binLabel}`, datum, y));
    }
  };

  return (
    <div ref={ref}>
      <ChartFrame yTitle={y.title} xTitle={x.title} series={[]} legendLabel="Bins">
        <BarsChart
          pivoted={pivoted}
          y={y}
          extras={tooltip}
          stacked={false}
          horizontal={false}
          contiguous
          tipLabels
          angledLabels={isCrowded}
          height={340}
          onPick={onPick}
        />
      </ChartFrame>
    </div>
  );
}
