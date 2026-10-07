"use client";

import { RefreshCwIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { isHttpUrl } from "@/lib/guards";
import type { Meta } from "@/lib/types";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-sm font-medium">{title}</h3>
      {children}
    </section>
  );
}

function filterChips(filters: Meta["filters"]): string[] {
  const chips: string[] = [];
  for (const [key, value] of Object.entries(filters)) {
    if (Array.isArray(value) && value.length > 0) {
      chips.push(`${key}: ${value.join(", ")}`);
    } else if (typeof value === "number" || (typeof value === "string" && value !== "")) {
      chips.push(`${key}: ${value}`);
    } else if (key === "exclude" && value && typeof value === "object") {
      for (const [name, left] of Object.entries(value as Record<string, string[]>)) {
        if (left.length > 0) chips.push(`excluding ${name}: ${left.join(", ")}`);
      }
    } else if (value && typeof value === "object" && !Array.isArray(value)) {
      // A comparison names its field and its values; any other object a later minor version adds is left out, not crashed on.
      const compare = value as { field?: unknown; values?: unknown };
      if (typeof compare.field === "string" && Array.isArray(compare.values)) {
        chips.push(`compare ${compare.field}: ${compare.values.join(" vs ")}`);
      }
    }
  }
  return chips;
}

/** Everything the response says about how the answer was made. */
export function TraceTab({ meta, onRerun, disabled }: { meta: Meta; onRerun?: () => void; disabled?: boolean }) {
  const { interpretation, counts, truncation, planner, source, timing, debug } = meta;
  const chips = filterChips(meta.filters);
  return (
    <div className="flex flex-col gap-6 text-sm">
      {interpretation ? (
        <Section title="Interpretation">
          <p>{interpretation.summary}</p>
          <p className="text-muted-foreground">{interpretation.chart_rationale}</p>
        </Section>
      ) : null}

      {interpretation && interpretation.entities.length > 0 ? (
        <Section title="Entities">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Kind</TableHead>
                  <TableHead>Text</TableHead>
                  <TableHead>Definition</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Trials</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {interpretation.entities.map((entity, index) => (
                  <TableRow key={index}>
                    <TableCell>{entity.kind}</TableCell>
                    <TableCell className="whitespace-normal">{entity.text}</TableCell>
                    <TableCell>{entity.definition}</TableCell>
                    <TableCell>{entity.status}</TableCell>
                    <TableCell className="text-right tabular-nums">{entity.trials_matched.toLocaleString("en-US")}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </Section>
      ) : null}

      {chips.length > 0 ? (
        <Section title="Filters applied">
          <div className="flex flex-wrap gap-1.5">
            {chips.map((chip) => (
              <Badge key={chip} variant="secondary">
                {chip}
              </Badge>
            ))}
          </div>
        </Section>
      ) : null}

      {interpretation && interpretation.adjustments.length > 0 ? (
        <Section title="Adjustments">
          <ul className="m-0 list-disc pl-5">
            {interpretation.adjustments.map((item, index) => (
              <li key={index}>
                {item.message} <span className="text-muted-foreground">({item.action})</span>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {meta.assumptions.length > 0 ? (
        <Section title="Assumptions">
          <ul className="m-0 list-disc pl-5">
            {meta.assumptions.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        </Section>
      ) : null}

      {meta.warnings.length > 0 ? (
        <Section title="Warnings">
          <ul className="m-0 list-disc pl-5">
            {meta.warnings.map((item, index) => (
              <li key={index}>
                {item.message} <code className="font-mono text-xs text-muted-foreground">{item.code}</code>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {counts ? (
        <Section title="Counts">
          <ul className="m-0 list-disc pl-5">
            {counts.series.map((series, index) => (
              <li key={index}>
                {series.label ?? "All trials"}: {series.trials_matched.toLocaleString("en-US")} matched,{" "}
                {series.trials_analyzed.toLocaleString("en-US")} analysed
                {series.trials_excluded.map((item) => `; ${item.count.toLocaleString("en-US")} excluded (${item.message})`).join("")}
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {truncation.is_truncated ? (
        <Section title="Truncation">
          <ul className="m-0 list-disc pl-5">
            {truncation.items.map((item, index) => (
              <li key={index}>
                {item.scope}: {item.shown.toLocaleString("en-US")} of {item.total.toLocaleString("en-US")}. {item.rule}
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      <Section title="Planner">
        <p>
          {planner.mode}
          {planner.model ? ` · ${planner.model}` : ""} · {planner.attempts} {planner.attempts === 1 ? "attempt" : "attempts"}
          {planner.is_repaired ? " · repaired" : ""}
          {planner.is_fallback ? " · fallback" : ""}
        </p>
      </Section>

      {debug && debug.trace.length > 0 ? (
        <Section title="Steps">
          <ol className="m-0 list-decimal pl-5">
            {debug.trace.map((step) => (
              <li key={step.index}>
                <span className="font-medium">{step.type}</span>: {step.summary}{" "}
                <span className="text-muted-foreground tabular-nums">{step.duration_ms} ms</span>
              </li>
            ))}
          </ol>
        </Section>
      ) : null}

      {source && source.requests.length > 0 ? (
        // A walk of the registry can take dozens of requests: they are all here, behind one line, so that the
        // rest of the trace (and the re-run button below it) is not pushed out of reach.
        <details>
          <summary className="cursor-pointer text-sm font-medium">Upstream requests ({source.requests.length})</summary>
          <ul className="m-0 mt-2 list-none p-0">
            {source.requests.map((request, index) => (
              <li key={index} className="break-all">
                {isHttpUrl(request.url) ? (
                  <a href={request.url} target="_blank" rel="noopener noreferrer" className="text-primary underline underline-offset-3 hover:no-underline">
                    {request.url}
                  </a>
                ) : (
                  request.url
                )}{" "}
                <span className="text-muted-foreground">
                  {request.origin} · {request.status} · {request.duration_ms} ms
                </span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      <Section title="Timing">
        <p className="tabular-nums">
          {timing.total_ms} ms in total: plan {timing.plan_ms}, resolve {timing.resolve_ms}, fetch {timing.fetch_ms}, build {timing.build_ms}.
        </p>
      </Section>

      {meta.plan && onRerun ? (
        <div>
          <Button variant="outline" size="sm" onClick={onRerun} disabled={disabled}>
            <RefreshCwIcon aria-hidden />
            Re-run this plan (no model)
          </Button>
        </div>
      ) : null}
    </div>
  );
}
