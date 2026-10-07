import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { FallbackView } from "@/components/viz/fallback";
import type { QueryResponse } from "@/lib/types";

import { barChart } from "./fixtures/responses";

afterEach(cleanup);

/** A response whose visualization type a later minor version added. */
function withUnknownType(): QueryResponse {
  const copy = structuredClone(barChart) as unknown as { visualization: { type: string } };
  copy.visualization.type = "heatmap";
  return copy as unknown as QueryResponse;
}

describe("FallbackView", () => {
  it("shows the rows of an unknown visualization type with their own values", () => {
    const { container } = render(<FallbackView response={withUnknownType()} />);
    const rows = container.querySelectorAll("tbody tr");
    expect(rows).toHaveLength(9);
    // The fixture's "Phase 2" bar has 88 trials behind it: the table shows that, not a placeholder zero.
    const headers = [...container.querySelectorAll("thead th")].map((header) => header.textContent);
    const phase2 = [...rows].find((row) => row.querySelector("td")?.textContent === "Phase 2");
    expect(phase2?.querySelectorAll("td")[headers.indexOf("citation_count")].textContent).toBe("88");
    expect(screen.getByText("Raw JSON")).toBeTruthy();
  });
});
