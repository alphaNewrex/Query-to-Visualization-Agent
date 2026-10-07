"use client";

import * as React from "react";

import { pivot } from "@/lib/pivot";

import { BarsChart } from "./bars";
import { ChartFrame, selection } from "./chart-kit";
import type { RendererProps } from "./renderer-props";

/**
 * One bar per row, labelled with the row's own `bin_label` (PLAN 5.4): bins may be uneven and the
 * last may be open-ended, so the renderer never computes an axis from `bin_start` and `bin_end`.
 */
export function HistogramView({ spec, onSelect }: RendererProps<"histogram">) {
  const { x, y, label, tooltip } = spec.encoding;
  const pivoted = React.useMemo(
    () => pivot(spec.data, { x: label.field, y: y.field, valueLabel: y.title }),
    [spec.data, label.field, y.field, y.title],
  );

  const onPick = (binLabel: string, seriesKey: string) => {
    const datum = pivoted.find(binLabel, seriesKey);
    if (datum !== undefined) {
      onSelect(selection(`${x.title}: ${binLabel}`, datum, y));
    }
  };

  return (
    <ChartFrame yTitle={y.title} xTitle={x.title} series={[]} legendLabel="Bins">
      <BarsChart pivoted={pivoted} y={y} extras={tooltip} stacked={false} horizontal={false} contiguous tipLabels height={340} onPick={onPick} />
    </ChartFrame>
  );
}
