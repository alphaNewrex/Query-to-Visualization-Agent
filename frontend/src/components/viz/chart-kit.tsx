"use client";

/**
 * Pieces shared by the four Recharts renderers: the series palette and chart config, the frame
 * with its axis captions and HTML legend, the tooltip panel, and a width hook.
 *
 * The legend is plain HTML in domain order. Recharts would sort a legend alphabetically and would
 * not wrap ten entries on a phone.
 */
import * as React from "react";

import type { ChartConfig } from "@/components/ui/chart";
import { cell } from "@/lib/cell";
import { formatCellValue, formatDatum } from "@/lib/format";
import type { PivotSeries, Pivoted } from "@/lib/pivot";
import type { Datum, DatumSelection, FieldDef, QuantitativeChannel } from "@/lib/types";

const PALETTE_SIZE = 10;

/** The colour of the series at this position of its domain: `--chart-1` to `--chart-10`, in order. */
export function seriesColor(index: number): string {
  return `var(--chart-${(index % PALETTE_SIZE) + 1})`;
}

/** The colour a chart element refers to for a positional series key (`s0`): set by `ChartContainer`. */
export function seriesFill(key: string): string {
  return `var(--color-${key})`;
}

/** Chart config keyed by the positional series keys, never by registry text. */
export function seriesConfig(series: readonly PivotSeries[]): ChartConfig {
  return Object.fromEntries(series.map((entry, index) => [entry.key, { label: entry.label, color: seriesColor(index) }]));
}

/** The width of an element, kept up to date. It stays 0 where `ResizeObserver` does not exist (tests). */
export function useElementWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = React.useRef<T | null>(null);
  const [width, setWidth] = React.useState(0);
  React.useEffect(() => {
    const element = ref.current;
    if (element === null || typeof ResizeObserver === "undefined") {
      return;
    }
    const observer = new ResizeObserver((entries) => {
      const next = Math.round(entries[0]?.contentRect.width ?? 0);
      setWidth((previous) => (previous === next ? previous : next));
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

// ---------------------------------------------------------------------------------------------
// Frame and legend
// ---------------------------------------------------------------------------------------------

export function SeriesLegend({ items, label }: { items: readonly { label: string; color: string }[]; label: string }) {
  return (
    <ul aria-label={label} className="m-0 flex list-none flex-wrap justify-center gap-x-4 gap-y-1 p-0 text-xs">
      {items.map((item, index) => (
        <li key={`${index}:${item.label}`} className="flex max-w-full items-center gap-1.5">
          <span aria-hidden className="size-2.5 shrink-0 rounded-[2px]" style={{ background: item.color }} />
          <span className="truncate text-foreground">{item.label}</span>
        </li>
      ))}
    </ul>
  );
}

/** Axis captions in HTML, a legend when there are two or more series, and the chart between. */
export function ChartFrame({
  yTitle,
  xTitle,
  series,
  legendLabel,
  children,
}: {
  yTitle: string;
  xTitle: string | null;
  series: readonly PivotSeries[];
  legendLabel: string;
  children: React.ReactNode;
}) {
  return (
    <figure className="m-0 flex flex-col gap-2">
      <figcaption className="text-xs text-muted-foreground">{yTitle}</figcaption>
      {children}
      {xTitle ? <div className="text-center text-xs text-muted-foreground">{xTitle}</div> : null}
      {series.length > 1 ? (
        <SeriesLegend
          label={legendLabel}
          items={series.map((entry, index) => ({ label: entry.label, color: seriesColor(index) }))}
        />
      ) : null}
    </figure>
  );
}

// ---------------------------------------------------------------------------------------------
// Tooltip
// ---------------------------------------------------------------------------------------------

export interface TooltipRow {
  /** The series colour, shown as a short stroke; null for a row that has no series. */
  color: string | null;
  label: string | null;
  value: string;
  extras: { label: string; value: string }[];
}

export function TooltipPanel({ title, rows }: { title: string | null; rows: readonly TooltipRow[] }) {
  return (
    <div className="grid min-w-36 max-w-72 gap-1.5 rounded-lg border border-border/60 bg-background px-2.5 py-2 text-xs shadow-xl">
      {title ? <div className="break-words font-medium text-foreground">{title}</div> : null}
      {rows.map((row, index) => (
        <div key={index} className="grid gap-0.5">
          <div className="flex items-center gap-2">
            {row.color ? (
              <span aria-hidden className="h-0.5 w-3 shrink-0 rounded-full" style={{ background: row.color }} />
            ) : null}
            <span className="font-mono font-semibold text-foreground tabular-nums">{row.value}</span>
            {row.label ? <span className="break-words text-muted-foreground">{row.label}</span> : null}
          </div>
          {row.extras.map((extra) => (
            <div key={extra.label} className={row.color ? "pl-5 text-muted-foreground" : "text-muted-foreground"}>
              {extra.label}: <span className="text-foreground">{extra.value}</span>
            </div>
          ))}
        </div>
      ))}
      <div className="border-t pt-1.5 text-[11px] text-muted-foreground">Click for the trials behind this value</div>
    </div>
  );
}

/** What the tooltip shows for one x value: one row per series that has a datum there. */
export function tooltipRowsAt(
  xLabel: string,
  pivoted: Pivoted,
  y: QuantitativeChannel,
  extras: readonly FieldDef[],
): TooltipRow[] {
  const showSeries = pivoted.series.length > 1;
  return pivoted.series.flatMap((entry, index) => {
    const datum = pivoted.find(xLabel, entry.key);
    if (datum === undefined) {
      return [];
    }
    return [
      {
        color: showSeries ? seriesColor(index) : null,
        label: showSeries ? entry.label : null,
        value: valueText(datum, y),
        extras: extraLines(datum, extras),
      },
    ];
  });
}

/** The extra tooltip fields of the encoding, as label and formatted value. */
export function extraLines(datum: Datum, defs: readonly FieldDef[]): { label: string; value: string }[] {
  return defs.map((def) => ({ label: def.title, value: formatCellValue(cell(datum, def.field), def) }));
}

/** A datum's value on a quantitative channel, formatted with its unit. */
export const valueText = formatDatum;

/** What a renderer reports for a selected mark. */
export function selection(label: string, datum: Datum, channel: QuantitativeChannel): DatumSelection {
  return { label, value: valueText(datum, channel), datum };
}

/** "Phase 2" for a single series, "Phase 2 · nivolumab" for one of several. */
export function markLabel(xLabel: string, seriesLabel: string | null): string {
  return seriesLabel === null ? xLabel : `${xLabel} · ${seriesLabel}`;
}

/** Greedy word wrap: a word longer than `maxChars` stands alone on its line. */
function wrapWords(text: string, maxChars: number): string[] {
  const lines: string[] = [];
  let line = "";
  for (const word of text.split(" ")) {
    if (line === "") {
      line = word;
    } else if (line.length + 1 + word.length <= maxChars) {
      line = `${line} ${word}`;
    } else {
      lines.push(line);
      line = word;
    }
  }
  return line === "" ? lines : [...lines, line];
}

/**
 * Splits an axis label into lines of at most `maxChars` characters. It breaks after a slash first,
 * so "Phase 1/Phase 2" becomes "Phase 1/" and "Phase 2", then at spaces.
 */
export function wrapLabel(label: string, maxChars: number): string[] {
  if (label.length <= maxChars) {
    return [label];
  }
  const groups = label.split("/");
  return groups.flatMap((group, index) => wrapWords(index < groups.length - 1 ? `${group}/` : group, maxChars));
}

/** A category axis tick that wraps onto up to `maxLines` lines instead of overprinting its neighbour. */
export function WrappedTick({
  x,
  y,
  payload,
  maxChars,
  maxLines,
}: {
  x?: number | string;
  y?: number | string;
  payload?: { value?: unknown };
  maxChars: number;
  maxLines: number;
}) {
  const lines = wrapLabel(String(payload?.value ?? ""), maxChars);
  const shown = lines.slice(0, maxLines);
  if (lines.length > maxLines) {
    shown[maxLines - 1] = truncate(`${shown[maxLines - 1]} ${lines.slice(maxLines).join(" ")}`, maxChars);
  }
  return (
    <g transform={`translate(${Number(x ?? 0)},${Number(y ?? 0)})`}>
      <text textAnchor="middle" fontSize={12} fill="var(--muted-foreground)">
        {shown.map((line, index) => (
          <tspan key={index} x={0} dy={index === 0 ? 12 : 14}>
            {line}
          </tspan>
        ))}
      </text>
    </g>
  );
}

export function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, Math.max(1, max - 1))}…` : text;
}
