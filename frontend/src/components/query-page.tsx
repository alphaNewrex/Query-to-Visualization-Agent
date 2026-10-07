"use client";

import * as React from "react";

import { ApiError, isAbortError, postAnalysis, postQuery } from "@/lib/api";
import { EMPTY_FORM, type FormValues, fromRequest, toRequest } from "@/lib/request-form";
import type { DatumSelection, QueryRequest, QueryResponse } from "@/lib/types";

import { CitationSheet } from "./citation-sheet";
import { ExampleChips } from "./example-chips";
import { ErrorCard } from "./outcome-card";
import { type FocusRequest, QueryForm } from "./query-form";
import { ResultSkeleton } from "./result-skeleton";
import { ResultView } from "./result-view";

type State =
  | { tag: "idle" }
  | { tag: "loading" }
  | { tag: "done"; response: QueryResponse; request: QueryRequest }
  | { tag: "error"; error: Error; request: QueryRequest };

/** The one page: form, examples, and whatever the last request produced. */
export function QueryPage() {
  const [values, setValues] = React.useState<FormValues>(EMPTY_FORM);
  const [state, setState] = React.useState<State>({ tag: "idle" });
  const [selection, setSelection] = React.useState<DatumSelection | null>(null);
  const [focus, setFocus] = React.useState<FocusRequest | null>(null);
  const controller = React.useRef<AbortController | null>(null);

  const run = React.useCallback(async (request: QueryRequest, replay?: () => Promise<QueryResponse>) => {
    controller.current?.abort();
    const mine = new AbortController();
    controller.current = mine;
    setValues(fromRequest(request));
    setSelection(null);
    setState({ tag: "loading" });
    try {
      const response = replay ? await replay() : await postQuery(request, mine.signal);
      if (mine.signal.aborted) {
        return;
      }
      setState({ tag: "done", response, request });
      if (response.kind === "clarification" && response.clarification.missing_fields.length > 0) {
        setFocus((previous) => ({ fields: response.clarification.missing_fields, nonce: (previous?.nonce ?? 0) + 1 }));
      }
    } catch (error) {
      if (isAbortError(error) || mine.signal.aborted) {
        return;
      }
      setState({ tag: "error", error: error instanceof Error ? error : new ApiError({ code: "unknown", message: "Unknown error.", status: 0 }), request });
    }
  }, []);

  const busy = state.tag === "loading";
  const rerun =
    state.tag === "done" && state.response.meta.plan
      ? () => {
          const { plan, options } = state.response.meta;
          if (plan) {
            void run(state.request, () => postAnalysis({ plan, options }, controller.current?.signal));
          }
        }
      : undefined;

  return (
    <div className="flex flex-col gap-6">
      <section className="flex flex-col gap-4" aria-label="Ask a question">
        <QueryForm values={values} onChange={setValues} onSubmit={() => void run(toRequest(values))} busy={busy} focus={focus} />
        <ExampleChips onRun={(request) => void run(request)} disabled={busy} />
      </section>

      <section aria-label="Answer" aria-busy={busy}>
        {state.tag === "idle" ? (
          <p className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
            Ask a question or pick an example. The answer appears here as a chart with its notes and the trials behind every value.
          </p>
        ) : null}
        {state.tag === "loading" ? <ResultSkeleton /> : null}
        {state.tag === "error" ? <ErrorCard error={state.error} onRetry={() => void run(state.request)} /> : null}
        {state.tag === "done" ? (
          <>
            <ResultView
              response={state.response}
              request={state.request}
              onSelect={setSelection}
              onRun={(request) => void run(request)}
              onRerun={rerun}
              busy={busy}
            />
            <CitationSheet selection={selection} response={state.response} onClose={() => setSelection(null)} />
          </>
        ) : null}
      </section>

      <footer className="border-t pt-4 text-xs text-muted-foreground">
        Source: ClinicalTrials.gov
        {state.tag === "done" && state.response.meta.source ? `, data as of ${state.response.meta.source.data_timestamp.slice(0, 10)}` : ""}. Counts are
        aggregated by this service.
      </footer>
    </div>
  );
}
