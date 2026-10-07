import type { Datum } from "./types";

export type Cell = string | number | boolean | null;

/**
 * Reads one field of a data row. A row is a `Datum`: the fields its encoding names, beside the
 * reserved keys `citations`, `citation_count` and `source_url`. Only own properties are read, so
 * a field name such as `constructor` cannot reach the prototype, and a list (the citations) is
 * never returned as if it were a cell.
 */
export function cell(row: Datum, field: string): Cell {
  if (!Object.hasOwn(row, field)) {
    return null;
  }
  const value = row[field];
  return Array.isArray(value) ? null : value;
}

/** The cell as a finite number, or null. */
export function cellNumber(row: Datum, field: string): number | null {
  const value = cell(row, field);
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** The cell as display text: strings as they are, numbers and booleans in JSON notation, null as "". */
export function cellText(row: Datum, field: string): string {
  const value = cell(row, field);
  return value === null ? "" : String(value);
}
