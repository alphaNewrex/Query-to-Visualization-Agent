import { cellNumber } from "./cell";
import type { Datum } from "./types";

/**
 * The contract's closed set of number formats (PLAN 5.3): ",d" whole numbers with thousands
 * separators, ".1f" one decimal, ".1%" a fraction shown as a percentage with one decimal, and
 * null for "no format stated". The locale is fixed so that output does not depend on the viewer.
 */
const LOCALE = "en-US";

const FORMATTERS: Record<string, Intl.NumberFormat> = {
  ",d": new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 0 }),
  ".1f": new Intl.NumberFormat(LOCALE, { minimumFractionDigits: 1, maximumFractionDigits: 1 }),
  ".1%": new Intl.NumberFormat(LOCALE, { style: "percent", minimumFractionDigits: 1, maximumFractionDigits: 1 }),
};

/** No format stated, or one a later minor version added: group thousands and keep up to two decimals. */
const DEFAULT_FORMATTER = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 2 });

export const MISSING = "–";

export interface Formatted {
  format: string | null;
  unit?: string | null;
}

export function formatNumber(value: number, format: string | null | undefined): string {
  if (!Number.isFinite(value)) {
    return MISSING;
  }
  const formatter = format != null && Object.hasOwn(FORMATTERS, format) ? FORMATTERS[format] : DEFAULT_FORMATTER;
  return formatter.format(value);
}

/** The contract gives units as plural nouns ("trials"); a count of exactly one reads better singular. */
function unitFor(value: number, unit: string): string {
  return value === 1 && unit.length > 2 && unit.endsWith("s") && !unit.endsWith("ss") ? unit.slice(0, -1) : unit;
}

/** A value with its unit when the channel has one: "1,234 trials", "28.5%". */
export function formatWithUnit(value: number, channel: Formatted): string {
  const text = formatNumber(value, channel.format);
  return channel.unit ? `${text} ${unitFor(value, channel.unit)}` : text;
}

/** Any cell of a row as text: numbers through the field's format, everything else as it is. */
export function formatCellValue(value: string | number | boolean | null, channel: Formatted): string {
  if (value === null) {
    return MISSING;
  }
  if (typeof value === "number") {
    return formatNumber(value, channel.format);
  }
  return String(value);
}

/** A row's value on a quantitative channel, with its unit; the placeholder when the row has none. */
export function formatDatum(datum: Datum, channel: Formatted & { field: string }): string {
  const value = cellNumber(datum, channel.field);
  return value === null ? MISSING : formatWithUnit(value, channel);
}

/** "2026-10-07T00:59:23.301713Z" as "2026-10-07 00:59 UTC": a timestamp of the contract is UTC, and the day alone would differ from the data date. */
export function utcMinute(timestamp: string): string {
  const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})[\d:.]*(?:Z|\+00:00)$/.exec(timestamp);
  return match ? `${match[1]} ${match[2]} UTC` : timestamp.slice(0, 10);
}
