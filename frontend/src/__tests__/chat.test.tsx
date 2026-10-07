// The chat against a stand-in backend: context, streamed steps, fallback and the new-conversation button.
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { QueryPage } from "@/components/query-page";
import type { QueryPlan, QueryResponse } from "@/lib/types";

import { clarification, timeSeries } from "./fixtures/responses";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } });
const planOf = (name: string) => ({ marker: name }) as unknown as QueryPlan;

function answerTo(query: string, extra: Partial<QueryResponse["meta"]> = {}): QueryResponse {
  return { ...timeSeries, message: `Answer to ${query}`, meta: { ...timeSeries.meta, query, plan: planOf(query), ...extra } };
}

interface Sent {
  path: string;
  body: Record<string, unknown>;
}

function backend(opts: { stream: boolean; fail?: string[]; reply?: (query: string) => QueryResponse }): Sent[] {
  const sent: Sent[] = [];
  const reply = opts.reply ?? ((query: string) => answerTo(query));
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input).replace("/api/backend/", "");
      if (path === "v1/capabilities") return json({ planner: { is_available: true, model: "m" } });
      if (path === "v1/examples") return json([{ slug: "01-a", query: "An example question", kind: "visualization", visualization_type: "time_series" }]);
      const body = JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>;
      sent.push({ path, body });
      if (path === "v1/query/stream" && !opts.stream) return json({ detail: "Not Found" }, 404);
      const query = String(body.query);
      if (opts.fail?.includes(query)) {
        return json({ error: { code: "upstream_error", message: "Registry down.", details: {}, request_id: "r", is_retryable: true } }, 502);
      }
      if (path === "v1/query") return json(reply(query));
      const encoder = new TextEncoder();
      const stage = (step: string, status: string, summary: string) => `event: stage\ndata: ${JSON.stringify({ step, status, summary, detail: {} })}\n\n`;
      return new Response(
        new ReadableStream({
          start(controller) {
            for (const text of [stage("plan", "done", "Understanding the question"), stage("build", "done", "Building the chart"), `event: result\ndata: ${JSON.stringify(reply(query))}\n\n`]) {
              controller.enqueue(encoder.encode(text));
            }
            controller.close();
          },
        }),
        { headers: { "content-type": "text/event-stream" } },
      );
    }),
  );
  return sent;
}

const composer = () => screen.getByRole("textbox", { name: /question about clinical trials/ });
async function say(text: string) {
  await userEvent.type(composer(), `${text}{Enter}`);
}
const queries = (sent: Sent[]) => sent.map((call) => call.body);

describe("a conversation", () => {
  it("sends the first message without context and the next with the last answer's question and plan", async () => {
    const sent = backend({ stream: true });
    render(<QueryPage />);
    await say("first question");
    await screen.findByText("Answer to first question");
    await say("second question");
    await screen.findByText("Answer to second question");
    await say("third question");
    await screen.findByText("Answer to third question");

    const bodies = queries(sent);
    expect(bodies[0]).toEqual({ query: "first question" });
    expect(bodies[1]).toEqual({ query: "second question", previous: { query: "first question", plan: planOf("first question") } });
    expect(bodies[2].previous).toEqual({ query: "second question", plan: planOf("second question") });
    // All three turns stay on the page.
    expect(screen.getAllByTestId("result")).toHaveLength(3);
    expect(composer()).toBeTruthy();
  });

  it("shows the steps that came in, folded into a Show steps disclosure once the answer is there", async () => {
    backend({ stream: true });
    render(<QueryPage />);
    await say("anything");
    await screen.findByText("Answer to anything");
    const disclosure = screen.getByTestId("steps-disclosure");
    expect(within(disclosure).getByText("Show steps")).toBeTruthy();
    expect(disclosure.textContent).toContain("Building the chart");
    expect((disclosure as HTMLDetailsElement).open).toBe(false);
  });

  it("falls back to plain POST /v1/query, with a skeleton and no steps, when there is no stream", async () => {
    const sent = backend({ stream: false });
    render(<QueryPage />);
    await say("anything");
    await screen.findByText("Answer to anything");
    expect(sent.map((call) => call.path)).toEqual(["v1/query/stream", "v1/query"]);
    expect(screen.queryByTestId("steps-disclosure")).toBeNull();
  });

  it("does not take a failed turn for the context", async () => {
    const sent = backend({ stream: true, fail: ["broken"] });
    render(<QueryPage />);
    await say("good");
    await screen.findByText("Answer to good");
    await say("broken");
    await screen.findByText("Registry down.");
    await say("after");
    await screen.findByText("Answer to after");
    expect(queries(sent).at(-1)?.previous).toEqual({ query: "good", plan: planOf("good") });
  });

  it("clears the thread and the context with New conversation", async () => {
    const sent = backend({ stream: true });
    render(<QueryPage />);
    await say("one");
    await screen.findByText("Answer to one");
    await userEvent.click(screen.getByRole("button", { name: "New conversation" }));
    expect(screen.queryByTestId("result")).toBeNull();
    expect(await screen.findByRole("button", { name: /^A, time series/ })).toBeTruthy();
    await say("two");
    await screen.findByText("Answer to two");
    expect(queries(sent).at(-1)).toEqual({ query: "two" });
  });

  it("shows what was understood on a follow-up", async () => {
    backend({
      stream: true,
      reply: (query) => answerTo(query, { conversation: { is_follow_up: true, carried_over: ["drug: X"], changed: ["split by phase"] } }),
    });
    render(<QueryPage />);
    await say("one");
    const chips = await screen.findByTestId("understood");
    expect(chips.textContent).toContain("Kept: drug: X");
    expect(chips.textContent).toContain("Changed: split by phase");
  });

  it("sends a suggested follow-up as the next turn, and a clarification option with the context", async () => {
    const sent = backend({
      stream: true,
      reply: (query) =>
        query === "vague"
          ? { ...clarification, meta: { ...clarification.meta, plan: planOf("vague") } }
          : answerTo(query, { suggested_followups: [{ label: "Split by phase", request: { query: "split it" } }] }),
    });
    render(<QueryPage />);
    await say("first");
    await userEvent.click(await screen.findByRole("button", { name: "Split by phase" }));
    await screen.findByText("Answer to split it");
    expect(screen.getByText("Split by phase", { selector: "p" })).toBeTruthy();
    expect(queries(sent).at(-1)?.previous).toEqual({ query: "first", plan: planOf("first") });

    await say("vague");
    await userEvent.click(await screen.findByRole("button", { name: "Nivolumab" }));
    await waitFor(() => expect(queries(sent).at(-1)?.query).toBe("How has the number of trials changed over time?"));
    expect(queries(sent).at(-1)?.previous).toEqual({ query: "vague", plan: planOf("vague") });
    expect(queries(sent).at(-1)?.drug_name).toEqual(["nivolumab"]);
  });

  it("sends on Enter and breaks the line on Shift+Enter", async () => {
    const sent = backend({ stream: true });
    render(<QueryPage />);
    await userEvent.type(composer(), "line one{Shift>}{Enter}{/Shift}line two");
    expect(sent).toHaveLength(0);
    await userEvent.keyboard("{Enter}");
    await screen.findByText(/^Answer to line one/);
    expect(sent[0].body.query).toBe("line one\nline two");
  });
});
