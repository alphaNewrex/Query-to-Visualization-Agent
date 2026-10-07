"use client";

import * as React from "react";

import { ApiError, getCapabilities, getExample, isAbortError, postAnalysis, postQuery, type RecordedExample } from "@/lib/api";
import { EMPTY_FORM, type FormValues, fromRequest, hasStructuredValues, toRequest } from "@/lib/request-form";
import type { AnalysisRequest, DatumSelection, QueryRequest, QueryResponse } from "@/lib/types";

import { CitationSheet } from "./citation-sheet";
import { ExampleGallery, SourceBar, type Source } from "./example-gallery";
import { ErrorCard } from "./outcome-card";
import { type FocusRequest, QueryForm } from "./query-form";
import { ResultSkeleton } from "./result-skeleton";
import { ResultView } from "./result-view";

/** An answer on show, with what was sent for it and how it came about. */
interface Shown {
  response: QueryResponse;
  /** What went to the backend; the JSON tab shows it. */
  sent: QueryRequest | AnalysisRequest;
  source: Source;
  /** The recorded example this answer belongs to: its chip stays selected and "Run live" has a request and a plan. */
  example: RecordedExample | null;
  /** A question to put into the form once the answer is there. */
  form?: QueryRequest;
}

type Send = (signal: AbortSignal) => Promise<Shown>;

type State =
  | { tag: "idle" }
  | { tag: "loading"; caption: string }
  | ({ tag: "done" } & Shown)
  | { tag: "error"; error: Error; send: Send; form?: QueryRequest };

const WAIT_FOR_ANSWER = "Planning, fetching from ClinicalTrials.gov and building the chart. This can take up to a minute.";

/** The one page: form, examples, and whatever the last request produced. */
export function QueryPage() {
  const [values, setValues] = React.useState<FormValues>(EMPTY_FORM);
  const [state, setState] = React.useState<State>({ tag: "idle" });
  const [selection, setSelection] = React.useState<DatumSelection | null>(null);
  const [focus, setFocus] = React.useState<FocusRequest | null>(null);
  const [reveal, setReveal] = React.useState(0);
  // Whether the server has a model, from GET /v1/capabilities; null until it is known.
  const [hasPlanner, setHasPlanner] = React.useState<boolean | null>(null);
  const controller = React.useRef<AbortController | null>(null);

  React.useEffect(() => {
    const mine = new AbortController();
    getCapabilities(mine.signal)
      .then((capabilities) => setHasPlanner(capabilities.planner?.is_available ?? null))
      .catch(() => undefined);
    return () => mine.abort();
  }, []);

  /** Puts a request that did not come from the form into it, and opens the structured fields when it fills one of them. */
  const mirror = React.useCallback((request: QueryRequest) => {
    const next = fromRequest(request);
    setValues(next);
    if (hasStructuredValues(next)) {
      setReveal((previous) => previous + 1);
    }
  }, []);

  /** Runs one job. A newer job replaces an older one that is still waiting. */
  const start = React.useCallback(
    async (send: Send, form?: QueryRequest, caption = WAIT_FOR_ANSWER) => {
      controller.current?.abort();
      const mine = new AbortController();
      controller.current = mine;
      if (form) {
        mirror(form);
      }
      setSelection(null);
      setState({ tag: "loading", caption });
      try {
        const shown = await send(mine.signal);
        if (mine.signal.aborted) {
          return;
        }
        if (shown.form) {
          mirror(shown.form);
        }
        setState({ tag: "done", ...shown });
        const { response } = shown;
        // A live clarification sends the reader to the missing field. A recording does not: the chip that
        // was just pressed keeps the focus, and the page does not jump back up to the form.
        if (shown.source !== "recorded" && response.kind === "clarification" && response.clarification.missing_fields.length > 0) {
          setFocus((previous) => ({ fields: response.clarification.missing_fields, nonce: (previous?.nonce ?? 0) + 1 }));
        }
      } catch (error) {
        if (isAbortError(error) || mine.signal.aborted) {
          return;
        }
        setState({
          tag: "error",
          error: error instanceof Error ? error : new ApiError({ code: "unknown", message: "Unknown error.", status: 0 }),
          send,
          form,
        });
      }
    },
    [mirror],
  );

  /** A question, live. One that the form sent is already in the form; a follow-up or a clarification option is put there. */
  const ask = (request: QueryRequest, fromForm = false) =>
    void start(
      async (signal) => ({ response: await postQuery(request, signal), sent: request, source: "live", example: null }),
      fromForm ? undefined : request,
    );

  /** A chip: the recording comes back from the backend and is shown as it is. */
  const pick = (slug: string) =>
    void start(
      async (signal) => {
        const example = await getExample(slug, signal);
        return { response: example.response, sent: example.request, source: "recorded", example, form: example.request };
      },
      undefined,
      "Loading the recorded answer.",
    );

  /** "Run live": the example's own request, structured fields included; with no model on the server, its recorded plan. */
  const runLive = (example: RecordedExample) =>
    void start(
      async (signal) => {
        if (hasPlanner === false) {
          const sent: AnalysisRequest = { plan: example.plan, options: example.response.meta.options };
          return { response: await postAnalysis(sent, signal), sent, source: "live-recorded-plan", example };
        }
        return { response: await postQuery(example.request, signal), sent: example.request, source: "live", example };
      },
      example.request,
    );

  const busy = state.tag === "loading";
  const done = state.tag === "done" ? state : null;
  const example = done?.example ?? null;

  const rerun =
    done && done.response.meta.plan
      ? () => {
          const { plan, options } = done.response.meta;
          if (plan) {
            const sent: AnalysisRequest = { plan, options };
            void start(async (signal) => ({
              response: await postAnalysis(sent, signal),
              sent,
              source: "live-same-plan",
              example,
            }));
          }
        }
      : undefined;

  return (
    <div className="flex flex-col gap-6">
      <section className="flex flex-col gap-4" aria-label="Ask a question">
        <QueryForm values={values} onChange={setValues} onSubmit={() => ask(toRequest(values), true)} busy={busy} focus={focus} reveal={reveal} />
        <ExampleGallery selected={example?.slug ?? null} onSelect={pick} />
      </section>

      <section aria-label="Answer" aria-busy={busy}>
        {state.tag === "idle" ? (
          <p className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
            {hasPlanner === false
              ? "This server has no model, so it cannot read a typed question. Pick a recorded example: it shows the answer at once, and Run live repeats it against ClinicalTrials.gov."
              : "Ask a question or pick a recorded example. The answer appears here as a chart with its notes and the trials behind every value."}
          </p>
        ) : null}
        {state.tag === "loading" ? <ResultSkeleton caption={state.caption} /> : null}
        {state.tag === "error" ? <ErrorCard error={state.error} onRetry={() => void start(state.send, state.form)} /> : null}
        {done ? (
          <>
            <SourceBar
              source={done.source}
              recordedAt={done.response.meta.generated_at}
              noPlanner={hasPlanner === false}
              onRunLive={done.source === "recorded" && example ? () => runLive(example) : null}
              busy={busy}
            />
            <ResultView
              response={done.response}
              request={done.sent}
              onSelect={setSelection}
              onRun={(request) => ask(request)}
              onRerun={rerun}
              busy={busy}
            />
            <CitationSheet selection={selection} response={done.response} onClose={() => setSelection(null)} />
          </>
        ) : null}
      </section>

      <footer className="border-t pt-4 text-xs text-muted-foreground">
        Source: ClinicalTrials.gov
        {done?.response.meta.source ? `, data as of ${done.response.meta.source.data_timestamp.slice(0, 10)}` : ""}. Counts are aggregated by this
        service.
      </footer>
    </div>
  );
}
