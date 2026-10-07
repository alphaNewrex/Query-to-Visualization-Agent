"use client";

import { ExternalLinkIcon } from "lucide-react";

import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { groupByTrial, trialLinks } from "@/lib/citations";
import { isHttpUrl } from "@/lib/guards";
import type { DatumSelection, QueryResponse } from "@/lib/types";

const LINK = "inline-flex items-center gap-1 text-primary underline underline-offset-3 hover:no-underline";

/** The trials behind a selected mark (PLAN 6.5). All registry text is rendered as text. */
export function CitationSheet({
  selection,
  response,
  onClose,
}: {
  selection: DatumSelection | null;
  response: QueryResponse;
  onClose: () => void;
}) {
  const datum = selection?.datum;
  const trials = datum ? groupByTrial(datum.citations) : [];
  const { source } = response.meta;

  return (
    <Sheet open={selection !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-full gap-0 overflow-y-auto sm:max-w-lg">
        <SheetHeader>
          <SheetTitle className="break-words">{selection?.label ?? "Citations"}</SheetTitle>
          <SheetDescription>
            {selection?.value ? <span className="font-medium text-foreground">{selection.value}. </span> : null}
            {datum
              ? `${trials.length} of ${datum.citation_count.toLocaleString("en-US")} ${datum.citation_count === 1 ? "trial" : "trials"} shown.`
              : null}
          </SheetDescription>
        </SheetHeader>
        <div className="flex flex-col gap-5 px-4 pb-6">
          {datum && trials.length === 0 ? (
            <p className="text-sm text-muted-foreground">No citations were sent for this value.</p>
          ) : null}
          {trials.map((trial) => {
            const reference = response.references[trial.nctId];
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
          {datum && isHttpUrl(datum.source_url) ? (
            <a href={datum.source_url} target="_blank" rel="noopener noreferrer" className={`${LINK} text-sm`}>
              All trials behind this value
              <ExternalLinkIcon className="size-3.5" aria-hidden />
            </a>
          ) : null}
        </div>
      </SheetContent>
    </Sheet>
  );
}
