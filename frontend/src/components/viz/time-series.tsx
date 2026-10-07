"use client";

import * as React from "react";
import { Area, AreaChart, CartesianGrid, Line, LineChart, Tooltip, XAxis, YAxis } from "recharts";
import type { DotItemDotProps } from "recharts";

import { ChartContainer } from "@/components/ui/chart";
import { formatNumber } from "@/lib/format";
import { pivot, type Pivoted } from "@/lib/pivot";
import type { FieldDef, QuantitativeChannel } from "@/lib/types";

import { BarsChart } from "./bars";
import { ChartFrame, markLabel, selection, seriesConfig, seriesFill, TooltipPanel, tooltipRowsAt } from "./chart-kit";
import type { RendererProps } from "./renderer-props";

const HEIGHT = 340;
const LINE_WIDTH = 2;
/** Markers are at least 8 px across (r 4); a dense series gets smaller ones. */
const DOT_RADIUS = 4;
const DENSE_DOT_RADIUS = 2.5;
const DENSE_AFTER = 40;
/** The transparent target around each point: a mark is never a pinpoint. */
const HIT_RADIUS = 12;

/**
 * A time series as a line, bars or areas (`mark`). Period labels (`2015`, `2024-Q2`, `2024-06`)
 * are drawn on a category axis and never parsed as dates (PLAN 5.1).
 */
export function TimeSeriesView({ spec, onSelect }: RendererProps<"time_series">) {
  const { x, y, series, tooltip } = spec.encoding;
  const pivoted = React.useMemo(
    () => pivot(spec.data, { x: x.field, y: y.field, series, valueLabel: y.title }),
    [spec.data, x.field, y.field, y.title, series],
  );

  const onPick = (period: string, seriesKey: string) => {
    const datum = pivoted.find(period, seriesKey);
    if (datum === undefined) {
      return;
    }
    const entry = pivoted.series.find((candidate) => candidate.key === seriesKey);
    onSelect(selection(markLabel(period, series && entry ? entry.label : null), datum, y));
  };

  return (
    <ChartFrame yTitle={y.title} xTitle={x.title} series={series ? pivoted.series : []} legendLabel={series?.title ?? "Series"}>
      {spec.mark === "bar" ? (
        <BarsChart
          pivoted={pivoted}
          y={y}
          extras={tooltip}
          stacked={spec.stack === "stacked"}
          horizontal={false}
          height={HEIGHT}
          onPick={onPick}
        />
      ) : (
        <LinesChart
          pivoted={pivoted}
          y={y}
          extras={tooltip}
          area={spec.mark === "area"}
          stacked={spec.mark === "area" && spec.stack === "stacked"}
          onPick={onPick}
        />
      )}
    </ChartFrame>
  );
}

interface LinesChartProps {
  pivoted: Pivoted;
  y: QuantitativeChannel;
  extras: readonly FieldDef[];
  area: boolean;
  stacked: boolean;
  onPick: (period: string, seriesKey: string) => void;
}

function LinesChart({ pivoted, y, extras, area, stacked, onPick }: LinesChartProps) {
  const config = React.useMemo(() => seriesConfig(pivoted.series), [pivoted.series]);
  const isLog = y.scale === "log";
  const dense = pivoted.rows.length > DENSE_AFTER;

  /**
   * Each point is drawn by this function as a circle with its own click handler, and the chart
   * sets `activeDot={false}`: with the default active dot, the highlight Recharts draws over a
   * hovered point lies on top of this circle and takes the click (PLAN 6.3).
   */
  const pointDot =
    (seriesKey: string, seriesLabel: string) => {
      const renderDot = ({ cx, cy, index, payload }: DotItemDotProps) => {
      const period: unknown = payload?.x;
      if (typeof cx !== "number" || typeof cy !== "number" || typeof period !== "string") {
        return <g key={`${seriesKey}-${index}`} />;
      }
      const datum = pivoted.find(period, seriesKey);
      const pick = () => onPick(period, seriesKey);
      return (
        <g
          key={`${seriesKey}-${index}`}
          role="button"
          tabIndex={0}
          aria-label={`${markLabel(period, pivoted.series.length > 1 ? seriesLabel : null)}: show the trials behind this point`}
          data-has-datum={datum !== undefined}
          className="cursor-pointer outline-none"
          onClick={pick}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              pick();
            }
          }}
        >
          <circle cx={cx} cy={cy} r={HIT_RADIUS} fill="transparent" />
          <circle
            cx={cx}
            cy={cy}
            r={dense ? DENSE_DOT_RADIUS : DOT_RADIUS}
            fill={seriesFill(seriesKey)}
            stroke="var(--card)"
            strokeWidth={2}
          />
        </g>
      );
      };
      return renderDot;
    };

  const axes = (
    <>
      <CartesianGrid vertical={false} />
      <XAxis
        dataKey="x"
        interval="preserveStartEnd"
        minTickGap={20}
        tickMargin={8}
        tickLine={false}
        axisLine={{ stroke: "var(--border)" }}
      />
      <YAxis
        scale={isLog ? "log" : "linear"}
        domain={isLog ? ["auto", "auto"] : [0, "auto"]}
        tickFormatter={(value: number) => formatNumber(value, y.format)}
        tickLine={false}
        axisLine={false}
        width={52}
        allowDecimals={y.format !== ",d"}
      />
      <Tooltip
        cursor={{ stroke: "var(--border)", strokeWidth: 1 }}
        isAnimationActive={false}
        content={(tip) => {
          if (!tip.active || !tip.payload?.length) {
            return null;
          }
          const period = String(tip.payload[0]?.payload?.x ?? "");
          return <TooltipPanel title={period} rows={tooltipRowsAt(period, pivoted, y, extras)} />;
        }}
      />
    </>
  );

  return (
    <ChartContainer config={config} className="aspect-auto w-full" style={{ height: HEIGHT }}>
      {area ? (
        <AreaChart data={pivoted.rows} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          {axes}
          {pivoted.series.map((entry) => (
            <Area
              key={entry.key}
              dataKey={entry.key}
              type="linear"
              stackId={stacked ? "stack" : undefined}
              stroke={seriesFill(entry.key)}
              strokeWidth={LINE_WIDTH}
              strokeLinejoin="round"
              fill={seriesFill(entry.key)}
              fillOpacity={0.12}
              dot={pointDot(entry.key, entry.label)}
              activeDot={false}
              isAnimationActive={false}
            />
          ))}
        </AreaChart>
      ) : (
        <LineChart data={pivoted.rows} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          {axes}
          {pivoted.series.map((entry) => (
            <Line
              key={entry.key}
              dataKey={entry.key}
              type="linear"
              stroke={seriesFill(entry.key)}
              strokeWidth={LINE_WIDTH}
              strokeLinecap="round"
              strokeLinejoin="round"
              dot={pointDot(entry.key, entry.label)}
              activeDot={false}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      )}
    </ChartContainer>
  );
}
