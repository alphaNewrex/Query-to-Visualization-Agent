import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CitationSheet } from "@/components/citation-sheet";
import { ResultView } from "@/components/result-view";
import { RENDERERS } from "@/components/viz/registry";
import type { DatumSelection, QueryResponse } from "@/lib/types";

import { allFixtures, barChart, chartFixtures, conversation, metric, network, table, timeSeries } from "./fixtures/responses";

afterEach(cleanup);

function show(response: QueryResponse, onSelect: (s: DatumSelection) => void = () => undefined) {
  return render(<ResultView response={response} request={null} onSelect={onSelect} onRun={() => undefined} />);
}

describe("registry", () => {
  it("has a renderer for each of the seven types", () => {
    expect(Object.keys(RENDERERS).sort()).toEqual(
      ["bar_chart", "histogram", "metric", "network_graph", "scatter_plot", "table", "time_series"],
    );
  });

  it.each(Object.entries(allFixtures))("renders the %s fixture", (_name, response) => {
    const { container } = show(response);
    expect(screen.getByText(response.message)).toBeTruthy();
    if (response.kind === "visualization") {
      expect(screen.getByRole("tab", { name: "Chart" })).toBeTruthy();
      // The error boundary's fallback would say so.
      expect(container.textContent).not.toContain("could not be drawn");
    } else {
      expect(container.querySelector(`[data-outcome="${response.kind}"]`)).not.toBeNull();
    }
  });

  it("shows a conversational reply as a plain message with a button for each suggested question", () => {
    const { container } = show(conversation);
    expect(container.querySelector('[role="alert"]')).toBeNull();
    expect(container.textContent).not.toMatch(/not supported/i);
    expect(screen.getByRole("button", { name: "How many recruiting trials are there for lung cancer?" })).toBeTruthy();
  });

  it("draws every table row and network node as its own mark", () => {
    const rows = show(table).container.querySelectorAll('[data-mark="row"]');
    expect(rows).toHaveLength(4);
    cleanup();
    const view = show(network).container;
    expect(view.querySelectorAll('[data-mark="node"]')).toHaveLength(11);
    expect(view.querySelectorAll('[data-mark="link"]')).toHaveLength(7);
  });

  it("shows the notes and follow-ups under the chart", () => {
    show(timeSeries);
    const notes = screen.getByTestId("notes");
    expect(within(notes).getByText(/2026 is incomplete/)).toBeTruthy();
    expect(within(notes).getByRole("button", { name: "Split by phase" })).toBeTruthy();
  });

  it("lists a statement once when the backend makes it both a warning and an assumption", () => {
    const repeated = structuredClone(timeSeries);
    repeated.meta.warnings = [{ code: "left_out", message: "Pembrolizumab is in most trials, so it is left out." }];
    repeated.meta.assumptions = ["Pembrolizumab is in most trials, so it is left out.", "Counts are per trial."];
    show(repeated);
    const notes = screen.getByTestId("notes");
    expect(within(notes).getAllByText("Pembrolizumab is in most trials, so it is left out.")).toHaveLength(1);
    expect(within(notes).getByText("Counts are per trial.")).toBeTruthy();
  });

  it("sends a follow-up's ready-made request", async () => {
    const onRun = vi.fn();
    render(<ResultView response={timeSeries} request={null} onSelect={() => undefined} onRun={onRun} />);
    await userEvent.click(screen.getByRole("button", { name: "Split by phase" }));
    expect(onRun).toHaveBeenCalledWith({ query: "Pembrolizumab trials per year by phase", drug_name: ["pembrolizumab"] }, "Split by phase");
  });
});

describe("selection reports the clicked mark's own datum", () => {
  it("time series: the third point", async () => {
    const onSelect = vi.fn();
    show(timeSeries, onSelect);
    await userEvent.click(screen.getByRole("button", { name: /^2017: show the trials/ }));
    expect(onSelect).toHaveBeenCalledOnce();
    const selected: DatumSelection = onSelect.mock.calls[0][0];
    expect(selected.label).toBe("2017");
    expect(selected.value).toBe("260 trials");
    expect(selected.datum.start_year).toBe("2017");
  });

  it("table: the third row", async () => {
    const onSelect = vi.fn();
    const { container } = show(table, onSelect);
    await userEvent.click(container.querySelectorAll('[data-mark="row"]')[2]);
    expect(onSelect.mock.calls[0][0].datum.enrollment).toBe(950);
  });

  it("table: the Citations button of the second row", async () => {
    const onSelect = vi.fn();
    const { container } = show(table, onSelect);
    const second = container.querySelectorAll('[data-mark="row"]')[1] as HTMLElement;
    await userEvent.click(within(second).getByRole("button"));
    expect(onSelect).toHaveBeenCalledOnce();
    expect(onSelect.mock.calls[0][0].datum.enrollment).toBe(1100);
  });

  it("network: a node that is not the first", async () => {
    const onSelect = vi.fn();
    show(network, onSelect);
    await userEvent.click(screen.getByRole("button", { name: /^Ataluren/ }));
    expect(onSelect.mock.calls[0][0].datum.id).toBe("drug:Ataluren");
  });

  it("metric: the button reports the one row", async () => {
    const onSelect = vi.fn();
    show(metric, onSelect);
    await userEvent.click(screen.getByRole("button", { name: /Show the trials/ }));
    expect(onSelect.mock.calls[0][0].datum.trial_count).toBe(61);
  });

  it("bar chart: a bar other than the first", async () => {
    const onSelect = vi.fn();
    const { container } = show(barChart, onSelect);
    const bars = container.querySelectorAll(".recharts-bar-rectangle");
    expect(bars.length).toBe(9);
    await userEvent.click(bars[3].querySelector("path, rect") ?? bars[3]);
    expect(onSelect.mock.calls[0][0].datum.phase).toBe("Phase 2");
  });

  it("grouped bars: the second series of a period", async () => {
    const onSelect = vi.fn();
    const { container } = show(chartFixtures.bar_grouped, onSelect);
    const second = container.querySelectorAll(".recharts-bar")[1];
    const bar = second.querySelectorAll(".recharts-bar-rectangle")[1];
    await userEvent.click(bar.querySelector("path, rect") ?? bar);
    const datum = onSelect.mock.calls[0][0].datum;
    expect([datum.phase, datum.group]).toEqual(["Phase 2", "nivolumab"]);
  });
});

describe("citation sheet", () => {
  it("shows the trial, its title, the field path, the excerpt and the source link", () => {
    const datum = (timeSeries.visualization as { data: DatumSelection["datum"][] }).data[0];
    render(<CitationSheet selection={{ label: "2015", value: "120 trials", datum }} response={timeSeries} onClose={() => undefined} />);
    const first = datum.citations[0];
    expect(screen.getByRole("link", { name: new RegExp(first.nct_id) }).getAttribute("href")).toBe(`https://clinicaltrials.gov/study/${first.nct_id}`);
    expect(screen.getAllByText(/Study of an investigational therapy/)).toHaveLength(2);
    expect(screen.getAllByText("protocolSection.statusModule.startDateStruct.date").length).toBeGreaterThan(0);
    expect(screen.getByText(/“2015-03”/)).toBeTruthy();
    expect(screen.getByRole("link", { name: /All trials behind this value/ })).toBeTruthy();
    expect(screen.getByText(/2 of 120 trials shown/)).toBeTruthy();
  });

  it("leaves out the 'In scope because' line when the response records no evidence for a trial", () => {
    const bare = structuredClone(timeSeries);
    for (const reference of Object.values(bare.references)) {
      reference.scope_evidence = [];
    }
    const datum = (bare.visualization as { data: DatumSelection["datum"][] }).data[0];
    render(<CitationSheet selection={{ label: "2015", value: "120 trials", datum }} response={bare} onClose={() => undefined} />);
    expect(screen.getByRole("link", { name: new RegExp(datum.citations[0].nct_id) })).toBeTruthy();
    expect(screen.queryByText(/In scope because/)).toBeNull();
    expect(screen.queryByText(/no evidence recorded/)).toBeNull();
  });

  it("shows the evidence when there is some", () => {
    const datum = (timeSeries.visualization as { data: DatumSelection["datum"][] }).data[0];
    render(<CitationSheet selection={{ label: "2015", value: "120 trials", datum }} response={timeSeries} onClose={() => undefined} />);
    expect(screen.getAllByText(/In scope because/).length).toBeGreaterThan(0);
  });
});
