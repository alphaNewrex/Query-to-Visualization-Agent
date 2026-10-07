"use client";

import * as React from "react";
import { Bar, BarChart, CartesianGrid, LabelList, Tooltip, XAxis, YAxis } from "recharts";

import { ChartContainer } from "@/components/ui/chart";
import { formatNumber } from "@/lib/format";
import type { Pivoted } from "@/lib/pivot";
import type { FieldDef, QuantitativeChannel } from "@/lib/types";

import { seriesConfig, seriesFill, tooltipRowsAt, TooltipPanel, truncate, WrappedTick } from "./chart-kit";

/** Bars never fill their slot: the data is the only thing allowed to be loud (dataviz mark spec). */
const MAX_BAR_SIZE = 24;
const CORNER = 4;
/** Average width of one character of the 12 px axis font, used to size the category axis. */
const CHAR_WIDTH = 6.6;

export interface BarsChartProps {
  pivoted: Pivoted;
  y: QuantitativeChannel;
  /** The encoding's extra tooltip fields. */
  extras: readonly FieldDef[];
  stacked: boolean;
  /** Bars grow to the right and the category axis is on the left (Recharts `layout="vertical"`). */
  horizontal: boolean;
  /** A histogram: the bins touch. */
  contiguous?: boolean;
  /** Write each bar's value at its tip. */
  tipLabels?: boolean;
  /** Vertical bars only: wrap the category labels to this many characters and lines, as the width allows. */
  categoryLabels?: { maxChars: number; maxLines: number };
  height: number;
  /** A bar was clicked: its x label and its positional series key. */
  onPick: (xLabel: string, seriesKey: string) => void;
}

export function categoryAxisWidth(labels: readonly string[], maxChars: number): number {
  const longest = labels.reduce((max, label) => Math.max(max, Math.min(label.length, maxChars)), 0);
  return Math.min(220, Math.max(64, Math.round(longest * CHAR_WIDTH + 16)));
}

/** One bar chart body for the bar chart, the bar form of a time series and the histogram. */
export function BarsChart({
  pivoted,
  y,
  extras,
  stacked,
  horizontal,
  contiguous = false,
  tipLabels = false,
  categoryLabels,
  height,
  onPick,
}: BarsChartProps) {
  const config = React.useMemo(() => seriesConfig(pivoted.series), [pivoted.series]);
  const isLog = y.scale === "log";
  const valueDomain: [number | "auto", number | "auto"] = isLog ? ["auto", "auto"] : [0, "auto"];
  const formatTick = (value: number) => formatNumber(value, y.format);
  const labelCount = pivoted.rows.length;
  const lastKey = pivoted.series[pivoted.series.length - 1]?.key;

  return (
    <ChartContainer config={config} className="aspect-auto w-full" style={{ height }}>
      <BarChart
        data={pivoted.rows}
        layout={horizontal ? "vertical" : "horizontal"}
        margin={{ top: tipLabels && !horizontal ? 20 : 8, right: tipLabels && horizontal ? 48 : 8, bottom: 0, left: 0 }}
        barCategoryGap={contiguous ? 0 : "22%"}
        barGap={2}
      >
        <CartesianGrid horizontal={!horizontal} vertical={horizontal} />
        {horizontal ? (
          <>
            <XAxis
              type="number"
              scale={isLog ? "log" : "linear"}
              domain={valueDomain}
              tickFormatter={formatTick}
              tickLine={false}
              axisLine={false}
              allowDecimals={y.format !== ",d"}
            />
            <YAxis
              type="category"
              dataKey="x"
              interval={0}
              width={categoryAxisWidth(
                pivoted.rows.map((row) => row.x),
                30,
              )}
              tickFormatter={(value: string) => truncate(String(value), 30)}
              tickLine={false}
              axisLine={false}
              tickMargin={6}
            />
          </>
        ) : (
          <>
            <XAxis
              dataKey="x"
              interval={labelCount > 14 ? "preserveStartEnd" : 0}
              minTickGap={12}
              tickMargin={8}
              tick={
                categoryLabels
                  ? (props: { x?: number | string; y?: number | string; payload?: { value?: unknown } }) => (
                      <WrappedTick {...props} maxChars={categoryLabels.maxChars} maxLines={categoryLabels.maxLines} />
                    )
                  : undefined
              }
              height={categoryLabels ? 20 + categoryLabels.maxLines * 14 : undefined}
              tickLine={false}
              axisLine={{ stroke: "var(--border)" }}
            />
            <YAxis
              scale={isLog ? "log" : "linear"}
              domain={valueDomain}
              tickFormatter={formatTick}
              tickLine={false}
              axisLine={false}
              width={52}
              allowDecimals={y.format !== ",d"}
            />
          </>
        )}
        <Tooltip
          cursor={{ fill: "var(--muted)", fillOpacity: 0.6 }}
          isAnimationActive={false}
          content={(tip) => {
            if (!tip.active || !tip.payload?.length) {
              return null;
            }
            const xLabel = String(tip.payload[0]?.payload?.x ?? "");
            return <TooltipPanel title={xLabel} rows={tooltipRowsAt(xLabel, pivoted, y, extras)} />;
          }}
        />
        {pivoted.series.map((entry) => (
          <Bar
            key={entry.key}
            dataKey={entry.key}
            fill={seriesFill(entry.key)}
            stackId={stacked ? "stack" : undefined}
            maxBarSize={contiguous ? undefined : MAX_BAR_SIZE}
            // The data end is rounded and the baseline square; in a stack only the last segment has a free end.
            radius={
              contiguous || (stacked && entry.key !== lastKey)
                ? 0
                : horizontal
                  ? [0, CORNER, CORNER, 0]
                  : [CORNER, CORNER, 0, 0]
            }
            // A surface-coloured edge is the gap between touching bars and stacked segments.
            stroke={stacked || contiguous ? "var(--card)" : undefined}
            strokeWidth={stacked || contiguous ? 1.5 : undefined}
            isAnimationActive={false}
            cursor="pointer"
            onClick={(item) => onPick(String(item.payload?.x ?? ""), entry.key)}
          >
            {tipLabels ? (
              <LabelList
                dataKey={entry.key}
                position={horizontal ? "right" : "top"}
                fill="var(--foreground)"
                fontSize={11}
                formatter={(value: unknown) => (typeof value === "number" ? formatTick(value) : "")}
              />
            ) : null}
          </Bar>
        ))}
      </BarChart>
    </ChartContainer>
  );
}
