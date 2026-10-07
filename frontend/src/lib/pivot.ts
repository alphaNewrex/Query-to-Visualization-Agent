import { cellNumber, cellText } from "./cell";
import type { Datum } from "./types";

/**
 * Long rows (one per x value and series, as the contract sends them) to wide rows (one per x
 * value with one key per series), which is the shape Recharts draws from.
 *
 * Series keys are positional: `s0`, `s1`, and so on, in the order of the series domain. They are
 * never registry text, because the shadcn chart component writes its config keys into a style
 * element without escaping them (PLAN 6.3). The x value sits under the fixed key `x`.
 */
export interface PivotSeries {
  /** `s0`, `s1`, ...: the key of this series in every wide row. */
  key: string;
  /** The domain value this key stands for. */
  label: string;
}

export interface WideRow {
  x: string;
  [seriesKey: string]: string | number | null;
}

export interface Pivoted {
  rows: WideRow[];
  series: PivotSeries[];
  /** The long row behind one point: its x value and its series key. */
  find(x: string, seriesKey: string): Datum | undefined;
}

export interface PivotOptions {
  /** Field holding the x label. */
  x: string;
  /** Field holding the value. */
  y: string;
  /** A category axis: every value, in display order. Left out for a time axis, whose rows already ascend. */
  xDomain?: readonly string[];
  /** The series channel: its field and its domain. Null or left out: one series. */
  series?: { field: string; domain: readonly string[] } | null;
  /** Label of the single series when there is no series channel. */
  valueLabel: string;
}

export function pivot(data: readonly Datum[], options: PivotOptions): Pivoted {
  const seriesLabels: string[] = options.series ? [...options.series.domain] : [options.valueLabel];
  const seriesIndex = new Map(seriesLabels.map((label, index) => [label, index]));
  const xOrder: string[] = options.xDomain ? [...options.xDomain] : [];
  const xKnown = new Set(xOrder);
  const longRows = new Map<string, Map<string, Datum>>();

  for (const row of data) {
    const x = cellText(row, options.x);
    if (!xKnown.has(x)) {
      xKnown.add(x);
      xOrder.push(x);
    }

    let index = 0;
    if (options.series) {
      const label = cellText(row, options.series.field);
      let known = seriesIndex.get(label);
      if (known === undefined) {
        // A value missing from the domain breaks the contract; keep it instead of dropping data.
        known = seriesLabels.length;
        seriesLabels.push(label);
        seriesIndex.set(label, known);
      }
      index = known;
    }

    const key = `s${index}`;
    let byKey = longRows.get(x);
    if (byKey === undefined) {
      byKey = new Map();
      longRows.set(x, byKey);
    }
    if (!byKey.has(key)) {
      byKey.set(key, row);
    }
  }

  const series = seriesLabels.map((label, index) => ({ key: `s${index}`, label }));
  const rows = xOrder.map((x) => {
    const wide: WideRow = { x };
    for (const { key } of series) {
      const long = longRows.get(x)?.get(key);
      wide[key] = long ? cellNumber(long, options.y) : null;
    }
    return wide;
  });

  return { rows, series, find: (x, seriesKey) => longRows.get(x)?.get(seriesKey) };
}
