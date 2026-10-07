"use client";

import * as React from "react";
import { ExternalLinkIcon, ListPlusIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { findDatum, FULL_CITATIONS, groupByTrial, trialLinks } from "@/lib/citations";
import { isHttpUrl } from "@/lib/guards";
import type { Datum, DatumSelection, QueryResponse } from "@/lib/types";

import { WaveLoader } from "./wave-loader";

const LINK = "inline-flex items-center gap-1 text-primary underline underline-offset-3 hover:no-underline";

/** Where "Show all" stands for one datum: the larger run is on its way, did not come, or gave this datum's trials. */
type More =
  | { tag: "loading" }
  | { tag: "failed"; reason: "error" | "changed" }
  | { tag: "done"; datum: Datum; references: QueryResponse["references"] };

const count = (n: number) => n.toLocaleString("en-US");

/**
 * The trials behind a selected mark (PLAN 6.5). All registry text is rendered as text. With `onLoadAll`, a
 * datum that has more trials than it cites gets a "Show all" button: the callback returns the same answer
 * run again with up to `FULL_CITATIONS` trials per datum, and the datum is looked up in it by its own values.
 */
export function CitationSheet({
  selection,
  response,
  onClose,
  onLoadAll,
}: {
  selection: DatumSelection | null;
  response: QueryResponse;
  onClose: () => void;
  onLoadAll?: (response: QueryResponse) => Promise<QueryResponse>;
}) {
  const selected = selection?.datum;
  // What was loaded belongs to the datum it was loaded for; another selection does not see it.
  const [more, setMore] = React.useState<{ for: Datum; state: More } | null>(null);
  const state = more && more.for === selected ? more.state : null;
  const datum = state?.tag === "done" ? state.datum : selected;
  const references = state?.tag === "done" ? { ...response.references, ...state.references } : response.references;
  const trials = datum ? groupByTrial(datum.citations) : [];
  const total = datum?.citation_count ?? 0;
  const { source } = response.meta;
  const isLoading = state?.tag === "loading";
  const canLoad =
    onLoadAll !== undefined &&
    response.meta.plan !== null &&
    response.meta.citations.is_enabled &&
    state?.tag !== "done" &&
    !(state?.tag === "failed" && state.reason === "changed") &&
    trials.length < Math.min(total, FULL_CITATIONS);
  // Everything the backend gives per datum is listed, and the datum has more: the rest is at its source.
  const hasRest = trials.length > 0 && trials.length < total && (state?.tag === "done" || trials.length >= FULL_CITATIONS);
  const sourceUrl = isHttpUrl(datum?.source_url) ? datum.source_url : null;

  const loadAll = () => {
    if (!selected || !onLoadAll || isLoading) {
      return;
    }
    const outcome = (next: More) => setMore((current) => (current?.for === selected ? { for: selected, state: next } : current));
    setMore({ for: selected, state: { tag: "loading" } });
    onLoadAll(response).then(
      (full) => {
        const found = findDatum(response, selected, full);
        outcome(found ? { tag: "done", datum: found, references: full.references } : { tag: "failed", reason: "changed" });
      },
      () => outcome({ tag: "failed", reason: "error" }),
    );
  };

  return (
    <Sheet open={selection !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-full gap-0 sm:max-w-lg">
        <SheetHeader>
          <SheetTitle className="break-words">{selection?.label ?? "Citations"}</SheetTitle>
          <SheetDescription aria-live="polite">
            {selection?.value ? <span className="font-medium text-foreground">{selection.value}. </span> : null}
            {datum ? `${count(trials.length)} of ${count(total)} ${total === 1 ? "trial" : "trials"} shown.` : null}
          </SheetDescription>
        </SheetHeader>
        {canLoad || state?.tag === "failed" || hasRest ? (
          <div className="flex flex-col items-start gap-2 px-4 pb-3" data-testid="citation-more">
            {canLoad ? (
              <Button variant="outline" size="sm" onClick={loadAll} aria-disabled={isLoading} aria-busy={isLoading} className={isLoading ? "cursor-progress" : undefined}>
                {isLoading ? <WaveLoader size={16} decorative /> : <ListPlusIcon aria-hidden />}
                {isLoading
                  ? "Loading the trials"
                  : total <= FULL_CITATIONS
                    ? `Show all ${count(total)} trials`
                    : `Show all (the first ${FULL_CITATIONS} of ${count(total)})`}
              </Button>
            ) : null}
            {state?.tag === "failed" ? (
              <p role="alert" className="text-sm text-destructive">
                {state.reason === "changed"
                  ? "The registry's data has changed since this answer was built, so its other trials cannot be matched to this value. Ask the question again to refresh it."
                  : `The other trials could not be loaded. The ${count(trials.length)} shown stay as they are.`}
              </p>
            ) : null}
            {hasRest ? (
              <p className="text-sm text-muted-foreground">
                The other {count(total - trials.length)} trials are not listed here.{" "}
                {sourceUrl ? (
                  <a href={sourceUrl} target="_blank" rel="noopener noreferrer" className={LINK}>
                    All trials behind this value
                    <ExternalLinkIcon className="size-3.5" aria-hidden />
                  </a>
                ) : null}
              </p>
            ) : null}
          </div>
        ) : null}
        <div className="flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto px-4 pb-6" data-testid="citation-list">
          {datum && trials.length === 0 ? (
            <p className="text-sm text-muted-foreground">No citations were sent for this value.</p>
          ) : null}
          {trials.map((trial) => {
            const reference = references[trial.nctId];
            const links = trialLinks(trial.nctId, source, reference?.url);
            const evidence = reference?.scope_evidence ?? [];
            return (
              <section key={trial.nctId} className="flex flex-col gap-2 border-b pb-5 last:border-b-0" data-trial={trial.nctId}>
                <div className="flex flex-col gap-0.5">
                  {links.study ? (
                    <a href={links.study} target="_blank" rel="noopener noreferrer" className={`${LINK} font-mono text-sm font-medium`}>
                      {trial.nctId}
                      <ExternalLinkIcon className="size-3" aria-hidden />
                    </a>
                  ) : (
                    <span className="font-mono text-sm font-medium">{trial.nctId}</span>
                  )}
                  {reference ? <span className="text-sm break-words">{reference.title}</span> : null}
                </div>
                <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
                  {trial.citations.map((citation, index) => (
                    <li key={index} className="rounded-md bg-muted/50 px-2.5 py-1.5 text-xs">
                      <code className="font-mono break-all text-muted-foreground">{citation.field}</code>
                      <div className="mt-0.5 break-words">
                        {citation.excerpt === null ? (
                          <span className="text-muted-foreground italic">field absent</span>
                        ) : (
                          <>“{citation.excerpt}”</>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
                {evidence.length > 0 ? (
                  <div className="text-xs text-muted-foreground">
                    <span className="font-medium">In scope because: </span>
                    {evidence.map((item, index) => (
                      <span key={index} className="block break-words">
                        {item.field === null || item.excerpt === null ? (
                          <>“{item.entity_text}” matched by ClinicalTrials.gov&apos;s own search.</>
                        ) : (
                          <>
                            “{item.entity_text}” in <code className="font-mono break-all">{item.field}</code>: “{item.excerpt}”
                          </>
                        )}
                      </span>
                    ))}
                  </div>
                ) : null}
                <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
                  {links.record ? (
                    <a href={links.record} target="_blank" rel="noopener noreferrer" className={LINK}>
                      API record
                    </a>
                  ) : null}
                  {links.fhir ? (
                    <a href={links.fhir} target="_blank" rel="noopener noreferrer" className={LINK}>
                      FHIR (pilot format, downloads a file)
                    </a>
                  ) : null}
                </div>
              </section>
            );
          })}
          {sourceUrl && !hasRest ? (
            <a href={sourceUrl} target="_blank" rel="noopener noreferrer" className={`${LINK} text-sm`}>
              All trials behind this value
              <ExternalLinkIcon className="size-3.5" aria-hidden />
            </a>
          ) : null}
        </div>
      </SheetContent>
    </Sheet>
  );
}
