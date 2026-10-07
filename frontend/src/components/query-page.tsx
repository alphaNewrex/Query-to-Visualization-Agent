"use client";

import * as React from "react";
import { MessageSquarePlusIcon } from "lucide-react";

import { ApiError, askQuery, getCapabilities, getExample, isAbortError, postAnalysis, type RecordedExample } from "@/lib/api";
import {
  type Answered,
  contextOf,
  EMPTY_THREAD,
  type Previous,
  threadReducer,
  type Turn,
} from "@/lib/conversation";
import { EMPTY_FORM, type FormValues, toRequest } from "@/lib/request-form";
import type { AnalysisRequest, DatumSelection, QueryRequest } from "@/lib/types";

import { Button } from "@/components/ui/button";

import { CitationSheet } from "./citation-sheet";
import { ExampleGallery } from "./example-gallery";
import { QueryForm } from "./query-form";
import { AssistantTurn, UserBubble } from "./turn-view";

type Job = (signal: AbortSignal) => Promise<Answered>;

const STOPPED = new ApiError({ code: "stopped", message: "Stopped before the answer arrived.", status: 0, isRetryable: true });

/** The chat: a thread of questions and answers with a composer below it. */
export function QueryPage() {
  const [thread, dispatch] = React.useReducer(threadReducer, EMPTY_THREAD);
  const [values, setValues] = React.useState<FormValues>(EMPTY_FORM);
  const [selection, setSelection] = React.useState<{ turnId: number; datum: DatumSelection } | null>(null);
  const [focusKey, setFocusKey] = React.useState(0);
  // Whether the server has a model, from GET /v1/capabilities; null until it is known.
  const [hasPlanner, setHasPlanner] = React.useState<boolean | null>(null);
  const controller = React.useRef<AbortController | null>(null);
  const counter = React.useRef(0);
  // What each turn runs, so that "Try again" repeats exactly that.
  const jobs = React.useRef(new Map<number, Job>());
  const end = React.useRef<HTMLDivElement>(null);

  const turns = thread.turns;
  const running = turns.find((turn) => turn.state.tag === "running") ?? null;
  const busy = running !== null;

  React.useEffect(() => {
    const mine = new AbortController();
    getCapabilities(mine.signal)
      .then((capabilities) => setHasPlanner(capabilities.planner?.is_available ?? null))
      .catch(() => undefined);
    return () => mine.abort();
  }, []);

  // A new turn scrolls to the end of the thread; an answer that has arrived scrolls to its own top,
  // so that a tall chart is read from its title and not from its bottom edge.
  const last = turns.at(-1);
  const lastSignature = last ? `${last.id}:${last.state.tag}` : "";
  React.useEffect(() => {
    if (!last) {
      return;
    }
    const smooth = !window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const behavior = smooth ? "smooth" : "auto";
    if (last.state.tag === "running") {
      end.current?.scrollIntoView?.({ behavior, block: "end" });
    } else {
      document.querySelector(`[data-turn="${last.id}"]`)?.scrollIntoView?.({ behavior, block: "start" });
    }
    // Only a change of the last turn or of its state moves the page, not each step that arrives.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastSignature]);

  /** Runs the job of one turn. A newer job replaces an older one that is still waiting. */
  const execute = async (id: number, job: Job) => {
    controller.current?.abort();
    const mine = new AbortController();
    controller.current = mine;
    jobs.current.set(id, job);
    try {
      const answered = await job(mine.signal);
      if (mine.signal.aborted) {
        return;
      }
      dispatch({ type: "done", id, answered });
      const { response } = answered;
      // A live clarification waits for its answer in the composer.
      if (answered.source !== "recorded" && response.kind === "clarification") {
        setFocusKey((key) => key + 1);
      }
    } catch (error) {
      if (isAbortError(error) || mine.signal.aborted) {
        return;
      }
      dispatch({
        type: "fail",
        id,
        error: error instanceof Error ? error : new ApiError({ code: "unknown", message: "Unknown error.", status: 0 }),
      });
    }
  };

  /** A question to the model, streamed when the backend can. Context goes along with it. */
  const liveJob =
    (id: number, request: QueryRequest, previous: Previous | null): Job =>
    async (signal) => {
      const { response, sent } = await askQuery(request, previous, {
        signal,
        onStage: (stage) => dispatch({ type: "stage", id, stage }),
        onFallback: () => dispatch({ type: "fallback", id }),
      });
      return { response, sent, source: "live", example: null };
    };

  const begin = (text: string, request: QueryRequest, previous: Previous | null, job: (id: number) => Job) => {
    const id = ++counter.current;
    dispatch({ type: "ask", id, text, request, previous });
    void execute(id, job(id));
  };

  /** A message of the reader: a typed question, a follow-up, or the option of a clarification. */
  const ask = (request: QueryRequest, label?: string) => {
    if (busy) {
      return;
    }
    const previous = contextOf(thread);
    begin(label ?? request.query, request, previous, (id) => liveJob(id, request, previous));
  };

  /** An example question: its recording comes back from the backend and is shown as it is. */
  const pick = (slug: string, query: string) => {
    if (busy) {
      return;
    }
    begin(query, { query }, null, () => async (signal) => {
      const example = await getExample(slug, signal);
      return { response: example.response, sent: example.request, source: "recorded", example };
    });
  };

  /** "Run live": the example's own request, structured fields included; with no model on the server, its recorded plan. */
  const runLive = (turn: Turn, example: RecordedExample) => {
    if (busy) {
      return;
    }
    dispatch({ type: "restart", id: turn.id, request: example.request, previous: null });
    const job: Job =
      hasPlanner === false
        ? async (signal) => {
            const sent: AnalysisRequest = { plan: example.plan, options: example.response.meta.options };
            return { response: await postAnalysis(sent, signal), sent, source: "live-recorded-plan", example };
          }
        : async (signal) => ({ ...(await liveJob(turn.id, example.request, null)(signal)), example });
    void execute(turn.id, job);
  };

  /** The Trace tab's "Re-run this plan": the plan of the answer runs again, without a model. */
  const rerun = (turn: Turn) => {
    if (busy || turn.state.tag !== "done" || !turn.state.response.meta.plan) {
      return;
    }
    const { plan, options } = turn.state.response.meta;
    const { example } = turn.state;
    const sent: AnalysisRequest = { plan, options };
    dispatch({ type: "restart", id: turn.id });
    void execute(turn.id, async (signal) => ({ response: await postAnalysis(sent, signal), sent, source: "live-same-plan", example }));
  };

  const retry = (turn: Turn) => {
    const job = jobs.current.get(turn.id);
    if (busy || !job) {
      return;
    }
    dispatch({ type: "restart", id: turn.id });
    void execute(turn.id, job);
  };

  const stop = () => {
    if (!running) {
      return;
    }
    controller.current?.abort();
    dispatch({ type: "fail", id: running.id, error: STOPPED });
  };

  const newConversation = () => {
    controller.current?.abort();
    jobs.current.clear();
    dispatch({ type: "reset" });
    setSelection(null);
    setValues(EMPTY_FORM);
    setFocusKey((key) => key + 1);
  };

  const selected = selection ? turns.find((turn) => turn.id === selection.turnId) : undefined;
  const selectedResponse = selected?.state.tag === "done" ? selected.state.response : null;
  const lastDone = turns.findLast((turn) => turn.state.tag === "done");
  const lastResponse = lastDone?.state.tag === "done" ? lastDone.state.response : null;
  const timestamp = lastResponse?.meta.source?.data_timestamp;

  return (
    <div className="flex min-h-dvh flex-1 flex-col">
      <header className="sticky top-0 z-20 border-b bg-background">
        <div className="mx-auto flex w-full max-w-4xl items-center justify-between gap-3 px-4 py-2.5 sm:px-6">
          <div className="min-w-0">
            <h1 className="truncate text-base font-semibold tracking-tight sm:text-lg">ClinicalTrials.gov Query-to-Visualization Agent</h1>
            <p className="hidden text-xs text-muted-foreground sm:block">Ask a question about clinical trials and get a chart with its sources.</p>
          </div>
          <Button variant="outline" size="sm" onClick={newConversation} disabled={turns.length === 0 && values.query === ""} className="shrink-0">
            <MessageSquarePlusIcon aria-hidden />
            New conversation
          </Button>
        </div>
      </header>

      <main className="mx-auto flex w-full max-w-4xl flex-1 flex-col gap-6 px-4 py-6 sm:px-6" aria-label="Conversation">
        {turns.length === 0 ? (
          <section className="flex flex-1 flex-col justify-center gap-6 py-8" aria-label="Start a conversation">
            <div className="flex flex-col gap-2">
              <h2 className="text-xl font-semibold tracking-tight">What would you like to know about clinical trials?</h2>
              <p className="text-sm text-muted-foreground">
                {hasPlanner === false
                  ? "This server has no model, so it cannot read a typed question. Pick an example: it shows the answer at once, and Run live repeats it against ClinicalTrials.gov."
                  : "Ask a question and the answer appears as a chart with its notes and the trials behind every value. Then keep going: each follow-up builds on the answer before it."}
              </p>
            </div>
            <ExampleGallery onSelect={pick} />
          </section>
        ) : (
          <div className="flex flex-col gap-8">
            {turns.map((turn) => (
              <div key={turn.id} data-turn={turn.id} className="flex scroll-mt-20 flex-col gap-3">
                <UserBubble text={turn.text} />
                <AssistantTurn
                  turn={turn}
                  busy={busy}
                  noPlanner={hasPlanner === false}
                  onSelect={(turnId, datum) => setSelection({ turnId, datum })}
                  onRun={ask}
                  onRunLive={(item) => item.state.tag === "done" && item.state.example && runLive(item, item.state.example)}
                  onRerun={rerun}
                  onRetry={retry}
                />
              </div>
            ))}
          </div>
        )}
        <footer className="mt-auto border-t pt-4 text-xs text-muted-foreground">
          Source: ClinicalTrials.gov{timestamp ? `, data as of ${timestamp.slice(0, 10)}` : ""}. Counts are aggregated by this service.
        </footer>
        <div ref={end} aria-hidden />
      </main>

      <div className="sticky bottom-0 z-10 border-t bg-background">
        <div className="mx-auto w-full max-w-4xl px-4 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:px-6">
          <QueryForm
            values={values}
            onChange={setValues}
            onSubmit={() => {
              ask(toRequest(values));
              setValues(EMPTY_FORM);
            }}
            onStop={stop}
            busy={busy}
            focusKey={focusKey}
            placeholder={turns.length === 0 ? "Ask about clinical trials" : "Ask a follow-up, or a new question"}
          />
        </div>
      </div>

      {selectedResponse ?? lastResponse ? (
        <CitationSheet selection={selected ? (selection?.datum ?? null) : null} response={(selectedResponse ?? lastResponse)!} onClose={() => setSelection(null)} />
      ) : null}
    </div>
  );
}
