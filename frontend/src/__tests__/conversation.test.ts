import { describe, expect, it } from "vitest";

import { type Answered, contextOf, conversationOf, EMPTY_THREAD, stepLabel, type Thread, type ThreadAction, threadReducer } from "@/lib/conversation";
import type { QueryPlan, QueryResponse } from "@/lib/types";

import { makeMeta } from "./fixtures/build";
import { noData, timeSeries } from "./fixtures/responses";

const plan = (name: string) => ({ marker: name }) as unknown as QueryPlan;

function answer(name: string, planned: QueryPlan | null = plan(name)): Answered {
  const response: QueryResponse = { ...timeSeries, meta: { ...timeSeries.meta, query: name, plan: planned } };
  return { response, sent: { query: name }, source: "live", example: null };
}

function run(actions: ThreadAction[], from: Thread = EMPTY_THREAD): Thread {
  return actions.reduce(threadReducer, from);
}

const ask = (id: number, query: string): ThreadAction => ({ type: "ask", id, text: query, request: { query }, previous: null });

describe("contextOf", () => {
  it("is null for an empty thread", () => {
    expect(contextOf(EMPTY_THREAD)).toBeNull();
  });

  it("is the question and plan of the last answered turn", () => {
    const thread = run([ask(1, "first"), { type: "done", id: 1, answered: answer("first") }, ask(2, "second"), { type: "done", id: 2, answered: answer("second") }]);
    expect(contextOf(thread)).toEqual({ query: "second", plan: plan("second") });
  });

  it("skips a turn that ended in an error, and one that is still running", () => {
    const thread = run([
      ask(1, "first"),
      { type: "done", id: 1, answered: answer("first") },
      ask(2, "second"),
      { type: "fail", id: 2, error: new Error("down") },
      ask(3, "third"),
    ]);
    expect(contextOf(thread)).toEqual({ query: "first", plan: plan("first") });
  });

  it("skips an answer that carries no plan", () => {
    const thread = run([ask(1, "first"), { type: "done", id: 1, answered: answer("first") }, ask(2, "second"), { type: "done", id: 2, answered: answer("second", null) }]);
    expect(contextOf(thread)?.query).toBe("first");
  });

  it("takes the plan of a clarification or a no-data answer when it has one", () => {
    const answered: Answered = { response: { ...noData, meta: { ...noData.meta, plan: plan("none") } }, sent: { query: "q" }, source: "live", example: null };
    const thread = run([ask(1, "q"), { type: "done", id: 1, answered }]);
    expect(contextOf(thread)).toEqual({ query: "q", plan: plan("none") });
  });

  it("is null again after a new conversation", () => {
    const thread = run([ask(1, "first"), { type: "done", id: 1, answered: answer("first") }]);
    expect(contextOf(thread)).not.toBeNull();
    expect(contextOf(run([{ type: "reset" }], thread))).toBeNull();
    expect(run([{ type: "reset" }], thread).turns).toEqual([]);
  });

  it("follows a turn that is run again: a retry that succeeds becomes the context", () => {
    const thread = run([ask(1, "first"), { type: "fail", id: 1, error: new Error("x") }]);
    expect(contextOf(thread)).toBeNull();
    const retried = run([{ type: "restart", id: 1 }, { type: "done", id: 1, answered: answer("first") }], thread);
    expect(contextOf(retried)?.query).toBe("first");
  });
});

describe("threadReducer", () => {
  it("keeps the context that was sent with a turn", () => {
    const previous = { query: "before", plan: plan("before") };
    const thread = run([{ type: "ask", id: 1, text: "Split by phase", request: { query: "again" }, previous }]);
    expect(thread.turns[0]).toMatchObject({ text: "Split by phase", request: { query: "again" }, previous, streaming: true, state: { tag: "running" } });
  });

  it("lists steps in the order they first appear, and updates a step in place", () => {
    const stage = (step: string, status: "started" | "done", summary = "") => ({ type: "stage", id: 1, stage: { step, status, summary, detail: {} } }) as const;
    const thread = run([
      ask(1, "q"),
      stage("plan", "started"),
      stage("plan", "done", "Understood"),
      stage("execute", "started", "Fetching 1 of 3"),
      stage("execute", "started", "Fetching 2 of 3"),
      stage("execute", "done"),
    ]);
    expect(thread.turns[0].steps).toEqual([
      { step: "plan", status: "done", summary: "Understood" },
      { step: "execute", status: "done", summary: "Fetching 2 of 3" },
    ]);
  });

  it("finishes the steps still running when the answer arrives, and fails them on an error", () => {
    const started = run([ask(1, "q"), { type: "stage", id: 1, stage: { step: "plan", status: "started", summary: "", detail: {} } }]);
    expect(run([{ type: "done", id: 1, answered: answer("q") }], started).turns[0].steps[0].status).toBe("done");
    expect(run([{ type: "fail", id: 1, error: new Error("x") }], started).turns[0].steps[0].status).toBe("failed");
  });

  it("drops the steps and the stream when it falls back", () => {
    const thread = run([ask(1, "q"), { type: "stage", id: 1, stage: { step: "plan", status: "started", summary: "", detail: {} } }, { type: "fallback", id: 1 }]);
    expect(thread.turns[0]).toMatchObject({ steps: [], streaming: false });
  });

  it("only touches the turn it names", () => {
    const thread = run([ask(1, "a"), { type: "done", id: 1, answered: answer("a") }, ask(2, "b"), { type: "restart", id: 1 }]);
    expect(thread.turns.map((turn) => turn.state.tag)).toEqual(["running", "running"]);
    expect(thread.turns.map((turn) => turn.text)).toEqual(["a", "b"]);
  });

  it("labels a step by its summary, and by a generic name until it has one", () => {
    expect(stepLabel({ step: "build", status: "started", summary: "" })).toBe("Building the chart");
    expect(stepLabel({ step: "build", status: "done", summary: "Built 12 points" })).toBe("Built 12 points");
    expect(stepLabel({ step: "novel", status: "done", summary: "" })).toBe("novel");
  });
});

describe("conversationOf", () => {
  it("reads meta.conversation", () => {
    const meta = { ...makeMeta("q"), conversation: { is_follow_up: true, carried_over: ["drug pembrolizumab", 3], changed: ["split by phase"] } } as never;
    expect(conversationOf(meta)).toEqual({ is_follow_up: true, carried_over: ["drug pembrolizumab"], changed: ["split by phase"] });
  });

  it("is null when the backend does not send it, or sends something else", () => {
    expect(conversationOf({ ...makeMeta("q"), conversation: undefined } as never)).toBeNull();
    expect(conversationOf({ ...makeMeta("q"), conversation: "yes" } as never)).toBeNull();
  });
});
