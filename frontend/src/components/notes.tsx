"use client";

import { InfoIcon, TriangleAlertIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { Meta, QueryRequest } from "@/lib/types";

export function Followups({
  items,
  onRun,
}: {
  items: Meta["suggested_followups"];
  onRun: (request: QueryRequest) => void;
}) {
  if (items.length === 0) {
    return null;
  }
  return (
    <div className="flex flex-wrap gap-2" aria-label="Suggested follow-ups">
      {items.map((item, index) => (
        <Button key={index} variant="outline" size="sm" onClick={() => onRun(item.request)}>
          {item.label}
        </Button>
      ))}
    </div>
  );
}

/** Compact list under the chart: the assumptions and warnings; the Trace tab keeps the detail. */
export function Notes({ meta, onRun }: { meta: Meta; onRun: (request: QueryRequest) => void }) {
  const hasNotes = meta.assumptions.length > 0 || meta.warnings.length > 0;
  if (!hasNotes && meta.suggested_followups.length === 0) {
    return null;
  }
  return (
    <div className="mt-4 flex flex-col gap-3" data-testid="notes">
      {hasNotes ? (
        <ul className="m-0 flex list-none flex-col gap-1.5 p-0 text-sm text-muted-foreground">
          {meta.warnings.map((warning, index) => (
            <li key={`w${index}`} className="flex gap-2">
              <TriangleAlertIcon className="mt-0.5 size-4 shrink-0 text-amber-600 dark:text-amber-400" aria-label="Warning" />
              <span>{warning.message}</span>
            </li>
          ))}
          {meta.assumptions.map((assumption, index) => (
            <li key={`a${index}`} className="flex gap-2">
              <InfoIcon className="mt-0.5 size-4 shrink-0" aria-label="Assumption" />
              <span>{assumption}</span>
            </li>
          ))}
        </ul>
      ) : null}
      <Followups items={meta.suggested_followups} onRun={onRun} />
    </div>
  );
}
