// "Show all" in the citation panel: it appears only when there is more to show, and loads the larger run of the same plan.
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CitationSheet } from "@/components/citation-sheet";
import { QueryPage } from "@/components/query-page";
import type { Datum, QueryPlan, QueryResponse } from "@/lib/types";

import { cite, nct, referencesFor } from "./fixtures/build";
import { timeSeries } from "./fixtures/responses";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

type Rows = Datum[];
const rowsOf = (response: QueryResponse): Rows => (response.visualization as unknown as { data: Rows }).data;
const PLAN = { marker: "the plan" } as unknown as QueryPlan;

/** The answer as the backend sends it: a plan in `meta`, and 2 trials cited for each of the rows. */
function answer(): QueryResponse {
  const response = structuredClone(timeSeries);
  response.meta = { ...response.meta, plan: PLAN, options: { use_cache: true } };
  return response;
}

/** The same answer run again with `shown` trials per row (at most the row's count): extra trials follow the cited ones. */
function larger(base: QueryResponse, shown: number): QueryResponse {
  const response = structuredClone(base);
  const rows = rowsOf(response);
  rows.forEach((row, index) => {
    const wanted = Math.min(row.citation_count, shown);
    const extra = Array.from({ length: Math.max(0, wanted - new Set(row.citations.map((c) => c.nct_id)).size) }, (_, n) =>
      cite(5000 + index * 1000 + n, "protocolSection.statusModule.startDateStruct.date", "2015-09"),
    );
    row.citations = [...row.citations, ...extra];
  });
  response.references = referencesFor(rows);
  return response;
}

const first = (response: QueryResponse) => rowsOf(response)[0];
const select = (response: QueryResponse) => ({ label: "2015", value: "120 trials", datum: first(response) });
const trialsListed = () => document.querySelectorAll("[data-trial]").length;

describe("Show all in the citation panel", () => {
  it("has no button when every trial of the datum is shown, or when nothing can be loaded", () => {
    const base = answer();
    const all = rowsOf(base)[0];
    all.citation_count = all.citations.length;
    render(<CitationSheet selection={select(base)} response={base} onClose={() => undefined} onLoadAll={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /show all/i })).toBeNull();
    cleanup();

    const more = answer();
    render(<CitationSheet selection={select(more)} response={more} onClose={() => undefined} />);
    expect(screen.queryByRole("button", { name: /show all/i })).toBeNull();
    cleanup();

    const planless = answer();
    planless.meta.plan = null;
    render(<CitationSheet selection={select(planless)} response={planless} onClose={() => undefined} onLoadAll={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /show all/i })).toBeNull();
  });

  it("offers the button with the count, shows the loader while it loads, and lists the larger set", async () => {
    const base = answer();
    let finish: (response: QueryResponse) => void = () => undefined;
    const onLoadAll = vi.fn(() => new Promise<QueryResponse>((resolve) => (finish = resolve)));
    render(<CitationSheet selection={select(base)} response={base} onClose={() => undefined} onLoadAll={onLoadAll} />);
    expect(trialsListed()).toBe(2);
    expect(screen.getByText(/2 of 120 trials shown/)).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Show all (the first 100 of 120)" }));
    const busy = screen.getByRole("button", { name: /loading the trials/i });
    expect(busy.getAttribute("aria-busy")).toBe("true");
    expect(busy.querySelector("svg circle")).not.toBeNull();
    await userEvent.click(busy);
    expect(onLoadAll).toHaveBeenCalledTimes(1);
    expect(onLoadAll).toHaveBeenCalledWith(base);

    finish(larger(base, 100));
    await waitFor(() => expect(trialsListed()).toBe(100));
    expect(screen.getByText(/100 of 120 trials shown/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /show all|loading the trials/i })).toBeNull();
    // Titles of the extra trials come from the larger answer's references; the list is the same format.
    const extra = document.querySelector(`[data-trial="${nct(5000)}"]`) as HTMLElement;
    expect(within(extra).getByRole("link", { name: new RegExp(nct(5000)) }).getAttribute("href")).toBe(`https://clinicaltrials.gov/study/${nct(5000)}`);
    expect(within(extra).getByText(`Study of an investigational therapy (${nct(5000)})`)).toBeTruthy();
    expect(within(extra).getByText("protocolSection.statusModule.startDateStruct.date")).toBeTruthy();
    expect(within(extra).getByText(/“2015-09”/)).toBeTruthy();
    // Past 100 the rest is not listed, and the datum's own source is the way to the complete list.
    expect(screen.getByText(/The other 20 trials are not listed here/)).toBeTruthy();
    expect(screen.getAllByRole("link", { name: /All trials behind this value/ })).toHaveLength(1);
  });

  it("names a button for a datum of at most 100 trials 'Show all N trials' and then lists them all", async () => {
    const base = answer();
    rowsOf(base)[0].citation_count = 7;
    const onLoadAll = vi.fn(async () => larger(base, 100));
    render(<CitationSheet selection={select(base)} response={base} onClose={() => undefined} onLoadAll={onLoadAll} />);
    await userEvent.click(screen.getByRole("button", { name: "Show all 7 trials" }));
    await waitFor(() => expect(trialsListed()).toBe(7));
    expect(screen.getByText(/7 of 7 trials shown/)).toBeTruthy();
    expect(screen.queryByText(/not listed here/)).toBeNull();
  });

  it("is reachable and operable from the keyboard", async () => {
    const base = answer();
    const onLoadAll = vi.fn(async () => larger(base, 100));
    render(<CitationSheet selection={select(base)} response={base} onClose={() => undefined} onLoadAll={onLoadAll} />);
    const button = screen.getByRole("button", { name: /show all/i });
    button.focus();
    expect(document.activeElement).toBe(button);
    await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(trialsListed()).toBe(100));
  });

  it("says '100 of N shown' and offers the source for a datum of more than 100 trials", async () => {
    const base = answer();
    rowsOf(base)[0].citation_count = 1234;
    render(<CitationSheet selection={select(base)} response={base} onClose={() => undefined} onLoadAll={async () => larger(base, 100)} />);
    await userEvent.click(screen.getByRole("button", { name: /Show all \(the first 100 of 1,234\)/ }));
    await waitFor(() => expect(trialsListed()).toBe(100));
    expect(screen.getByText(/100 of 1,234 trials shown/)).toBeTruthy();
    const link = screen.getByRole("link", { name: /All trials behind this value/ });
    expect(link.getAttribute("href")).toBe(first(base).source_url);
  });

  it("keeps the shown trials and says so when the larger run fails", async () => {
    const base = answer();
    const onLoadAll = vi.fn().mockRejectedValueOnce(new Error("boom")).mockResolvedValueOnce(larger(base, 100));
    render(<CitationSheet selection={select(base)} response={base} onClose={() => undefined} onLoadAll={onLoadAll} />);
    await userEvent.click(screen.getByRole("button", { name: /show all/i }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/could not be loaded/);
    expect(trialsListed()).toBe(2);
    expect(screen.getByText(/2 of 120 trials shown/)).toBeTruthy();
    // The button is still there to try again.
    await userEvent.click(screen.getByRole("button", { name: /show all/i }));
    await waitFor(() => expect(trialsListed()).toBe(100));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("finds the datum by its own values, not by its position", async () => {
    const base = answer();
    const run = larger(base, 100);
    rowsOf(run).reverse();
    render(<CitationSheet selection={select(base)} response={base} onClose={() => undefined} onLoadAll={async () => run} />);
    await userEvent.click(screen.getByRole("button", { name: /show all/i }));
    await waitFor(() => expect(trialsListed()).toBe(100));
    // The row of 2015 has the trials that were made for it, not those of the row now at its position.
    expect(document.querySelector(`[data-trial="${nct(5000)}"]`)).not.toBeNull();
    expect(document.querySelector(`[data-trial="${nct(5000 + 11 * 1000)}"]`)).toBeNull();
  });

  it("refuses a row of other values or another count, and keeps what it shows", async () => {
    for (const change of [(row: Datum) => (row.start_year = "2014"), (row: Datum) => (row.trial_count = 121), (row: Datum) => (row.citation_count = 121)]) {
      const base = answer();
      const run = larger(base, 100);
      change(first(run));
      render(<CitationSheet selection={select(base)} response={base} onClose={() => undefined} onLoadAll={async () => run} />);
      await userEvent.click(screen.getByRole("button", { name: /show all/i }));
      expect((await screen.findByRole("alert")).textContent).toMatch(/data has changed/);
      expect(trialsListed()).toBe(2);
      expect(screen.queryByRole("button", { name: /show all/i })).toBeNull();
      cleanup();
    }
  });

  it("matches a network node by its id and a link by its source and target", async () => {
    const { network } = await import("./fixtures/responses");
    const base = structuredClone(network);
    base.meta = { ...base.meta, plan: PLAN };
    const graph = (response: QueryResponse) => (response.visualization as unknown as { data: { nodes: Datum[]; edges: Datum[] } }).data;
    const edge = graph(base).edges[0];
    edge.citation_count = 40;
    const run = structuredClone(base);
    graph(run).edges.reverse();
    graph(run).edges.find((item) => item.id === edge.id)!.citations.push(cite(9001), cite(9002));
    render(<CitationSheet selection={{ label: "link", value: "40 trials", datum: edge }} response={base} onClose={() => undefined} onLoadAll={async () => run} />);
    await userEvent.click(screen.getByRole("button", { name: /show all 40 trials/i }));
    await waitFor(() => expect(document.querySelector(`[data-trial="${nct(9002)}"]`)).not.toBeNull());
  });
});

// ---------------------------------------------------------------------------------------------

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } });

describe("Show all in the chat", () => {
  it("runs the plan of the turn with 100 citations once, and reuses it for the next mark", async () => {
    const base = answer();
    const run = larger(base, 100);
    const analyses: Record<string, unknown>[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input).replace("/api/backend/", "");
        if (path === "v1/capabilities") return json({ planner: { is_available: true, model: "m" } });
        if (path === "v1/examples") return json([]);
        const body = JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>;
        if (path === "v1/analyses") {
          analyses.push(body);
          return json(run);
        }
        return json(base);
      }),
    );
    render(<QueryPage />);
    await userEvent.type(screen.getByRole("textbox", { name: /question about clinical trials/ }), "a question{Enter}");
    await screen.findByText(base.message);
    await userEvent.click(screen.getByRole("tab", { name: "Data" }));

    const open = async (index: number) => userEvent.click((await screen.findAllByRole("button", { name: /citations/i }))[index]);
    await open(0);
    await userEvent.click(await screen.findByRole("button", { name: /show all/i }));
    await waitFor(() => expect(trialsListed()).toBe(100));
    expect(analyses).toEqual([{ plan: PLAN, options: { use_cache: true, citations_per_datum: 100 } }]);

    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(trialsListed()).toBe(0));
    await open(1);
    await userEvent.click(await screen.findByRole("button", { name: /show all/i }));
    await waitFor(() => expect(trialsListed()).toBe(100));
    expect(analyses).toHaveLength(1);
  });
});
