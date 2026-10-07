// The example gallery against a stand-in backend that serves the committed examples (PLAN 6.2).
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { QueryPage } from "@/components/query-page";
import { withLabels } from "@/lib/examples";
import type { QueryRequest, QueryResponse } from "@/lib/types";

import { examples } from "./fixtures/examples.gen";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ASSIGNMENT = "01-assignment-request";

/**
 * What the recorded request of an example held: only the first carries a structured field. The
 * recording writes `drug_name` as a plain string, which the backend accepts, although its generated
 * type names a list.
 */
function requestOf(slug: string): QueryRequest {
  const query = examples[slug].meta.query ?? "";
  return (slug === ASSIGNMENT ? { query, drug_name: "Pembrolizumab" } : { query }) as unknown as QueryRequest;
}

interface Call {
  method: string;
  path: string;
  body: unknown;
}

function json(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } });
}

/** Stands in for the backend behind the proxy: `fetch` answers from the committed examples and keeps every call. */
function backend({
  planner,
  failExampleOnce = false,
  liveSlug = ASSIGNMENT,
}: {
  planner: boolean;
  failExampleOnce?: boolean;
  /** The recording that a live question is answered with. */
  liveSlug?: string;
}): Call[] {
  const calls: Call[] = [];
  let failed = false;
  const live = (response: QueryResponse, message: string, mode: string): QueryResponse => ({
    ...response,
    message,
    meta: { ...response.meta, planner: { ...response.meta.planner, mode: mode as QueryResponse["meta"]["planner"]["mode"] } },
  });
  const answer = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const path = String(input).replace("/api/backend/", "");
    const method = init?.method ?? "GET";
    calls.push({ method, path, body: typeof init?.body === "string" ? JSON.parse(init.body) : null });
    if (path === "v1/capabilities") {
      return json({ planner: { is_available: planner, model: planner ? "fixture-model" : null } });
    }
    if (path === "v1/examples") {
      return json(
        Object.entries(examples).map(([slug, response]) => ({
          slug,
          query: response.meta.query,
          kind: response.kind,
          visualization_type: response.visualization?.type ?? null,
        })),
      );
    }
    if (path.startsWith("v1/examples/")) {
      if (failExampleOnce && !failed) {
        failed = true;
        return json({ error: { code: "backend_unreachable", message: "The backend did not answer.", details: {}, request_id: "req-1", is_retryable: true } }, 502);
      }
      const slug = path.slice("v1/examples/".length);
      return json({ slug, request: requestOf(slug), plan: examples[slug].meta.plan, response: examples[slug] });
    }
    if (path === "v1/query") {
      return json(live(examples[liveSlug], "A live answer.", "llm"));
    }
    if (path === "v1/analyses") {
      return json(live(examples[ASSIGNMENT], "A replayed answer.", "supplied_plan"));
    }
    return json({ error: { code: "not_found", message: "Not found.", details: {}, request_id: "req-2", is_retryable: false } }, 404);
  };
  vi.stubGlobal("fetch", vi.fn(answer));
  return calls;
}

const posts = (calls: Call[]) => calls.filter((call) => call.method === "POST");
const chip = (label: RegExp) => screen.findByRole("button", { name: label });

describe("the chips", () => {
  it("come from the listing, one short label each, and two that share a question stay apart", async () => {
    backend({ planner: true });
    render(<QueryPage />);
    const first = await chip(/^Assignment request/);
    const last = await chip(/^No drug named/);
    // The same question text, told apart by the label alone.
    expect(first.getAttribute("title")).toBe(last.getAttribute("title"));
    const labels = screen.getAllByRole("button", { pressed: false }).map((button) => button.textContent);
    expect(labels).toHaveLength(Object.keys(examples).length);
    expect(new Set(labels).size).toBe(labels.length);
  });

  it("name each recording after its slug, and keep the number only to separate equal names", () => {
    const listing = [
      { slug: "02-compare-phases", query: "q", outcome: "bar_chart" },
      { slug: "10-no-drug-named", query: "q", outcome: "clarification" },
    ];
    expect(withLabels(listing).map((item) => item.label)).toEqual(["Compare phases", "No drug named"]);
    const same = [
      { slug: "01-largest-trials", query: "q", outcome: "table" },
      { slug: "02-largest-trials", query: "q", outcome: "table" },
    ];
    expect(withLabels(same).map((item) => item.label)).toEqual(["01-largest-trials", "02-largest-trials"]);
  });

  it("say so when the listing cannot be read", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ error: { code: "backend_unreachable", message: "Down.", details: {}, request_id: "r", is_retryable: true } }, 502)));
    render(<QueryPage />);
    expect(await screen.findByText("The recorded examples could not be loaded.")).toBeTruthy();
  });
});

describe("selecting a chip", () => {
  it("shows the recorded answer at once, with a Recorded badge, and sends nothing to the model", async () => {
    const calls = backend({ planner: true });
    render(<QueryPage />);
    await userEvent.click(await chip(/^Assignment request/));

    const bar = await screen.findByTestId("source-bar");
    expect(within(bar).getByText("Recorded")).toBeTruthy();
    expect(within(bar).getByRole("button", { name: "Run live" })).toBeTruthy();
    const viz = examples[ASSIGNMENT].visualization;
    expect(screen.getByRole("heading", { name: viz?.title })).toBeTruthy();
    expect(posts(calls)).toEqual([]);
    expect(calls.map((call) => call.path)).toContain(`v1/examples/${ASSIGNMENT}`);
    // The chip stays marked while its answer is on show.
    expect(screen.getByRole("button", { name: /^Assignment request/ }).getAttribute("aria-pressed")).toBe("true");
  });

  it("puts the example's own request into the form, structured field included", async () => {
    backend({ planner: true });
    render(<QueryPage />);
    await userEvent.click(await chip(/^Assignment request/));
    await screen.findByTestId("source-bar");
    expect((screen.getByRole("textbox", { name: /question about clinical trials/ }) as HTMLTextAreaElement).value).toBe(examples[ASSIGNMENT].meta.query);
    expect((screen.getByLabelText("Drug") as HTMLInputElement).value).toBe("Pembrolizumab");
  });

  it("shows the recorded clarification without moving the focus off the chip", async () => {
    backend({ planner: true });
    render(<QueryPage />);
    const button = await chip(/^No drug named/);
    await userEvent.click(button);
    await screen.findByTestId("source-bar");
    expect(document.querySelector('[data-outcome="clarification"]')).not.toBeNull();
    expect(document.activeElement).toBe(button);
  });

  it("offers a retry when the recording cannot be fetched", async () => {
    backend({ planner: true, failExampleOnce: true });
    render(<QueryPage />);
    await userEvent.click(await chip(/^Assignment request/));
    await userEvent.click(await screen.findByRole("button", { name: "Try again" }));
    expect(within(await screen.findByTestId("source-bar")).getByText("Recorded")).toBeTruthy();
  });
});

describe("Run live", () => {
  it("sends the example's own request, structured fields included, to /v1/query", async () => {
    const calls = backend({ planner: true });
    render(<QueryPage />);
    await userEvent.click(await chip(/^Assignment request/));
    await userEvent.click(await screen.findByRole("button", { name: "Run live" }));

    expect(await screen.findByText("A live answer.")).toBeTruthy();
    expect(posts(calls)).toEqual([{ method: "POST", path: "v1/query", body: requestOf(ASSIGNMENT) }]);
    expect(within(screen.getByTestId("source-bar")).getByText("Live")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Run live" })).toBeNull();
  });

  it("posts the recorded plan to /v1/analyses when the server has no model, and says live data, recorded plan", async () => {
    const calls = backend({ planner: false });
    render(<QueryPage />);
    // The idle text changes once the capabilities are known.
    await screen.findByText(/cannot read a typed question/);
    await userEvent.click(await chip(/^Assignment request/));
    const bar = await screen.findByTestId("source-bar");
    expect(bar.textContent).toContain("This server has no model, so Run live runs the recorded plan on live data.");
    await userEvent.click(within(bar).getByRole("button", { name: "Run live" }));

    expect(await screen.findByText("A replayed answer.")).toBeTruthy();
    const { plan, options } = examples[ASSIGNMENT].meta;
    expect(posts(calls)).toEqual([{ method: "POST", path: "v1/analyses", body: { plan, options } }]);
    expect(within(screen.getByTestId("source-bar")).getByText("Live data, recorded plan")).toBeTruthy();
  });

  it("re-runs the plan of an answer from the Trace tab, and says live data, same plan", async () => {
    const calls = backend({ planner: true });
    render(<QueryPage />);
    await userEvent.click(await chip(/^Assignment request/));
    await screen.findByTestId("source-bar");
    await userEvent.click(screen.getByRole("tab", { name: "Trace" }));
    await userEvent.click(screen.getByRole("button", { name: /Re-run this plan/ }));

    expect(await screen.findByText("A replayed answer.")).toBeTruthy();
    expect(posts(calls).map((call) => call.path)).toEqual(["v1/analyses"]);
    expect(within(screen.getByTestId("source-bar")).getByText("Live data, same plan")).toBeTruthy();
  });
});

describe("a typed question", () => {
  it("goes to /v1/query and is shown as live", async () => {
    const calls = backend({ planner: true });
    render(<QueryPage />);
    await userEvent.type(screen.getByRole("textbox", { name: /question about clinical trials/ }), "How many trials for pembrolizumab?");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));

    expect(await screen.findByText("A live answer.")).toBeTruthy();
    expect(posts(calls)).toEqual([{ method: "POST", path: "v1/query", body: { query: "How many trials for pembrolizumab?" } }]);
    expect(within(screen.getByTestId("source-bar")).getByText("Live")).toBeTruthy();
  });

  it("sends the reader to the field that a live clarification names as missing", async () => {
    backend({ planner: true, liveSlug: "10-no-drug-named" });
    render(<QueryPage />);
    await userEvent.type(screen.getByRole("textbox", { name: /question about clinical trials/ }), "How many trials for this drug?");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    await screen.findByText("A live answer.");
    await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText("Drug")));
  });

  it("points to the recorded examples when the server has no model", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) =>
        String(input).endsWith("v1/query")
          ? json({ error: { code: "planner_unavailable", message: "No planning model is configured.", details: {}, request_id: "req-3", is_retryable: false } }, 503)
          : json([]),
      ),
    );
    render(<QueryPage />);
    await userEvent.type(screen.getByRole("textbox", { name: /question about clinical trials/ }), "How many trials?");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    expect(await screen.findByText(/The recorded examples above need none/)).toBeTruthy();
    expect(screen.getByText(/planner_unavailable/)).toBeTruthy();
  });
});
