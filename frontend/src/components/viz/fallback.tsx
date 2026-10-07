"use client";

import { isRecord } from "@/lib/guards";
import type { Datum, FieldDef, QueryResponse } from "@/lib/types";

import { DataTable } from "./table-view";

const MAX_COLUMNS = 8;

/**
 * Shown for a response the client cannot draw: a visualization type or major version it does not
 * know, or a specification the boundary caught. It still gives the reader the message, the rows
 * as a table where there are any, and the raw JSON (PLAN 5.9).
 */
export function FallbackView({ response, reason }: { response: QueryResponse; reason?: string }) {
  const visualization: unknown = response.visualization;
  const records = isRecord(visualization) && Array.isArray(visualization.data) ? visualization.data.filter(isRecord) : [];
  const keys = new Set<string>();
  for (const record of records.slice(0, 10)) {
    for (const [key, value] of Object.entries(record)) {
      if (key !== "citations" && (value === null || typeof value !== "object")) {
        keys.add(key);
      }
    }
  }
  const columns: FieldDef[] = [...keys].slice(0, MAX_COLUMNS).map((field) => ({
    field,
    title: field,
    type: "nominal",
    unit: null,
    format: null,
    href_field: null,
  }));
  // The defaults come first, so that a row's own citation count and source URL are shown as they are.
  const rows = records.map((record) => ({ citations: [], citation_count: 0, source_url: null, ...record }) as Datum);

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">
        {reason ?? "This answer is in a form this page cannot draw."} Its rows and its JSON are shown instead.
      </p>
      {columns.length > 0 ? (
        <DataTable columns={columns} rows={rows} rowLabel={(_, index) => `Row ${index + 1}`} rowValue={() => ""} />
      ) : null}
      <details className="rounded-lg border bg-muted/30 p-3 text-xs">
        <summary className="cursor-pointer font-medium">Raw JSON</summary>
        <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(response, null, 2)}</pre>
      </details>
    </div>
  );
}
