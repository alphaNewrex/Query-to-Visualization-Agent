/**
 * The conversation: what is sent along with a follow-up, what the backend says it understood, and
 * the state of the thread. The contract types are generated from the schema and do not have these
 * fields yet, so they are declared and read defensively here.
 */
import type { RecordedExample } from "./api";
import { isRecord } from "./guards";
import type { StageEvent } from "./stream";
import type { AnalysisRequest, Meta, QueryPlan, QueryRequest, QueryResponse } from "./types";

/** The answer before: its question and the canonical plan of `meta.plan`. */
export interface Previous {
  query: string | null;
  plan: QueryPlan;
}

/** A question as POST /v1/query accepts it once the backend knows `previous`. */
export type ChatRequest = QueryRequest & { previous?: Previous };

/** `meta.conversation`: what the backend understood of a follow-up. */
export interface ConversationInfo {
  is_follow_up: boolean;
  carried_over: string[];
  changed: string[];
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && item.trim() !== "") : [];
}

/** `meta.conversation`, or null when the backend does not send it. */
export function conversationOf(meta: Meta): ConversationInfo | null {
  const value: unknown = (meta as { conversation?: unknown }).conversation;
  if (!isRecord(value) || typeof value.is_follow_up !== "boolean") {
    return null;
  }
  return { is_follow_up: value.is_follow_up, carried_over: strings(value.carried_over), changed: strings(value.changed) };
}

// ---------------------------------------------------------------------------------------------
// Steps
// ---------------------------------------------------------------------------------------------

export interface Step {
  step: string;
  status: "started" | "done" | "failed";
  summary: string;
}

const DEFAULT_LABELS: Record<string, string> = {
  plan: "Understanding the question",
  check: "Checking the plan",
  resolve: "Looking up the entities",
  strategy: "Choosing how to fetch",
  execute: "Fetching trials",
  build: "Building the chart",
};

/** What a step says: the backend's own words, and a generic label until it has any. */
export function stepLabel(step: Step): string {
  return step.summary.trim() || DEFAULT_LABELS[step.step] || step.step;
}

// ---------------------------------------------------------------------------------------------
// The thread
// ---------------------------------------------------------------------------------------------

/** How an answer came about; see SourceBar. */
export type AnswerSource = "recorded" | "live" | "live-recorded-plan" | "live-same-plan";

export interface Answered {
  response: QueryResponse;
  /** What went to the backend; the JSON tab shows it. */
  sent: QueryRequest | ChatRequest | AnalysisRequest;
  source: AnswerSource;
  /** The recorded example this answer belongs to: "Run live" has a request and a plan from it. */
  example: RecordedExample | null;
}

export type TurnState = { tag: "running" } | ({ tag: "done" } & Answered) | { tag: "error"; error: Error };

export interface Turn {
  id: number;
  /** The user's message as the bubble shows it. */
  text: string;
  /** What was asked, without the context. */
  request: QueryRequest;
  /** The context that was sent with it. */
  previous: Previous | null;
  steps: Step[];
  /** False once the stream proved unavailable: the turn waits behind a skeleton instead. */
  streaming: boolean;
  state: TurnState;
}

export interface Thread {
  turns: Turn[];
}

export const EMPTY_THREAD: Thread = { turns: [] };

export type ThreadAction =
  | { type: "ask"; id: number; text: string; request: QueryRequest; previous: Previous | null }
  | { type: "stage"; id: number; stage: StageEvent }
  | { type: "fallback"; id: number }
  | { type: "done"; id: number; answered: Answered }
  | { type: "fail"; id: number; error: Error }
  /** Runs a turn again, in place: the retry of an error, "Run live" or a re-run of the plan. */
  | { type: "restart"; id: number; request?: QueryRequest; previous?: Previous | null }
  | { type: "reset" };

function update(thread: Thread, id: number, change: (turn: Turn) => Turn): Thread {
  return { turns: thread.turns.map((turn) => (turn.id === id ? change(turn) : turn)) };
}

function settle(steps: Step[], status: "done" | "failed"): Step[] {
  return steps.map((step) => (step.status === "started" ? { ...step, status } : step));
}

export function threadReducer(thread: Thread, action: ThreadAction): Thread {
  switch (action.type) {
    case "ask":
      return {
        turns: [
          ...thread.turns,
          {
            id: action.id,
            text: action.text,
            request: action.request,
            previous: action.previous,
            steps: [],
            streaming: true,
            state: { tag: "running" },
          },
        ],
      };
    case "stage":
      return update(thread, action.id, (turn) => {
        const { step, status, summary } = action.stage;
        const index = turn.steps.findIndex((item) => item.step === step);
        if (index === -1) {
          return { ...turn, steps: [...turn.steps, { step, status, summary }] };
        }
        // A later event of the same step replaces it, but an empty summary keeps the words it had.
        const steps = turn.steps.map((item, i) => (i === index ? { step, status, summary: summary.trim() ? summary : item.summary } : item));
        return { ...turn, steps };
      });
    case "fallback":
      return update(thread, action.id, (turn) => ({ ...turn, streaming: false, steps: [] }));
    case "done":
      return update(thread, action.id, (turn) => ({ ...turn, steps: settle(turn.steps, "done"), state: { tag: "done", ...action.answered } }));
    case "fail":
      return update(thread, action.id, (turn) => ({ ...turn, steps: settle(turn.steps, "failed"), state: { tag: "error", error: action.error } }));
    case "restart":
      return update(thread, action.id, (turn) => ({
        ...turn,
        request: action.request ?? turn.request,
        previous: action.previous === undefined ? turn.previous : action.previous,
        steps: [],
        streaming: true,
        state: { tag: "running" },
      }));
    case "reset":
      return EMPTY_THREAD;
  }
}

/**
 * The context for the next question: the question and plan of the last turn that was answered.
 * A turn that ended in an error, is still running, or has no plan (a recorded answer without one)
 * is skipped, so a failed question never becomes the thing a follow-up refers to.
 */
export function contextOf(thread: Thread): Previous | null {
  for (let i = thread.turns.length - 1; i >= 0; i--) {
    const { state, request } = thread.turns[i];
    // A greeting or thanks has no scope to carry: skipping it keeps the last real answer open to follow-ups.
    if (state.tag === "done" && state.response.kind !== "conversation" && state.response.meta.plan) {
      return { query: request.query ?? null, plan: state.response.meta.plan };
    }
  }
  return null;
}
