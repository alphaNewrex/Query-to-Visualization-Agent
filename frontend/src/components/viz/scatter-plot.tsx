"use client";

import * as React from "react";
import { CartesianGrid, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from "recharts";

import { ChartContainer } from "@/components/ui/chart";
import { cellNumber, cellText } from "@/lib/cell";
import { formatNumber, formatWithUnit } from "@/lib/format";
import type { Datum, QuantitativeChannel } from "@/lib/types";

import {
  ChartFrame,
  extraLines,
  seriesColor,
  seriesConfig,
  seriesFill,
  TooltipPanel,
  type TooltipRow,
} from "./chart-kit";
import type { RendererProps } from "./renderer-props";

const HEIGHT = 380;
/** Symbol area in px²: a point is about 9 px across, the 2 px surface ring included. */
const POINT_AREA = 64;
const SIZE_RANGE: [number, number] = [48, 320];

interface Group {
  key: string;
  label: string;
  rows: Datum[];
}

/** One point per trial. Both coordinates are required, so a row missing either is not drawn. */
export function ScatterPlotView({ spec, onSelect }: RendererProps<"scatter_plot">) {
  const { x, y, series, size, label, tooltip } = spec.encoding;

  const groups = React.useMemo<Group[]>(() => {
    const drawable = spec.data.filter((row) => cellNumber(row, x.field) !== null && cellNumber(row, y.field) !== null);
    if (series === null) {
      return [{ key: "s0", label: y.title, rows: drawable }];
    }
    const labels = [...series.domain];
    for (const row of drawable) {
      const value = cellText(row, series.field);
      if (!labels.includes(value)) {
        labels.push(value);
      }
    }
    return labels.map((value, index) => ({
      key: `s${index}`,
      label: value,
      rows: drawable.filter((row) => cellText(row, series.field) === value),
    }));
  }, [spec.data, x.field, y.field, y.title, series]);

  const config = React.useMemo(() => seriesConfig(groups), [groups]);
  const hasSeries = series !== null;

  const markTitle = (row: Datum): string =>
    (label ? cellText(row, label.field) : "") || cellText(row, "nct_id") || "Trial";

  const markValue = (row: Datum): string => {
    const xv = cellNumber(row, x.field);
    const yv = cellNumber(row, y.field);
    return `${x.title} ${xv === null ? "–" : formatWithUnit(xv, x)} · ${y.title} ${yv === null ? "–" : formatWithUnit(yv, y)}`;
  };

  const isLog = (channel: QuantitativeChannel) => channel.scale === "log";

  /** A log axis gets whole powers of ten as ticks and as its ends; Recharts' automatic ticks are arbitrary there. */
  const axisScale = (channel: QuantitativeChannel) => {
    if (!isLog(channel)) {
      return { scale: "linear" as const, domain: ["auto", "auto"] as [string, string], ticks: undefined };
    }
    const values = spec.data.flatMap((row) => {
      const value = cellNumber(row, channel.field);
      return value !== null && value > 0 ? [value] : [];
    });
    const low = Math.floor(Math.log10(Math.min(...values, 1)));
    const high = Math.max(low + 1, Math.ceil(Math.log10(Math.max(...values, 1))));
    const ticks = Array.from({ length: high - low + 1 }, (_, i) => 10 ** (low + i));
    return { scale: "log" as const, domain: [ticks[0], ticks[ticks.length - 1]] as [number, number], ticks };
  };
  const xAxis = axisScale(x);
  const yAxis = axisScale(y);

  return (
    <ChartFrame yTitle={y.title} xTitle={x.title} series={hasSeries ? groups : []} legendLabel={series?.title ?? "Series"}>
      <ChartContainer config={config} className="aspect-auto w-full" style={{ height: HEIGHT }}>
        <ScatterChart margin={{ top: 12, right: 20, bottom: 0, left: 0 }}>
          <CartesianGrid />
          <XAxis
            type="number"
            dataKey={x.field}
            name={x.title}
            scale={xAxis.scale}
            domain={xAxis.domain}
            ticks={xAxis.ticks}
            tickFormatter={(value: number) => formatNumber(value, x.format)}
            tickLine={false}
            axisLine={{ stroke: "var(--border)" }}
            tickMargin={8}
          />
          <YAxis
            type="number"
            dataKey={y.field}
            name={y.title}
            scale={yAxis.scale}
            domain={yAxis.domain}
            ticks={yAxis.ticks}
            tickFormatter={(value: number) => formatNumber(value, y.format)}
            tickLine={false}
            axisLine={false}
            width={52}
          />
          <ZAxis type="number" dataKey={size?.field} range={size ? SIZE_RANGE : [POINT_AREA, POINT_AREA]} />
          <Tooltip
            cursor={{ stroke: "var(--border)", strokeDasharray: "3 3" }}
            isAnimationActive={false}
            content={(tip) => {
              const row = tip.active ? (tip.payload?.[0]?.payload as Datum | undefined) : undefined;
              if (row === undefined) {
                return null;
              }
              const groupIndex = groups.findIndex((group) => group.rows.includes(row));
              const color = hasSeries && groupIndex >= 0 ? seriesColor(groupIndex) : null;
              const rows: TooltipRow[] = [
                { color, label: hasSeries && groupIndex >= 0 ? groups[groupIndex].label : null, value: markValue(row), extras: [] },
              ];
              if (size) {
                rows.push({ color: null, label: size.title, value: formatWithUnit(cellNumber(row, size.field) ?? NaN, size), extras: [] });
              }
              return <TooltipPanel title={markTitle(row)} rows={[{ ...rows[0], extras: extraLines(row, tooltip) }, ...rows.slice(1)]} />;
            }}
          />
          {groups.map((group) => (
            <Scatter
              key={group.key}
              name={group.label}
              data={group.rows}
              fill={seriesFill(group.key)}
              fillOpacity={0.85}
              stroke="var(--card)"
              strokeWidth={1.5}
              isAnimationActive={false}
              cursor="pointer"
              onClick={(item) => {
                const row = item.payload as Datum | undefined;
                if (row !== undefined) {
                  onSelect({ label: markTitle(row), value: markValue(row), datum: row });
                }
              }}
            />
          ))}
        </ScatterChart>
      </ChartContainer>
    </ChartFrame>
  );
}
