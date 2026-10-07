import { describe, expect, it } from "vitest";

import { formatNumber, formatWithUnit } from "@/lib/format";
import { pivot } from "@/lib/pivot";

import { datum } from "./fixtures/build";

describe("formatNumber", () => {
  it("covers the closed set of formats", () => {
    expect(formatNumber(1234567, ",d")).toBe("1,234,567");
    expect(formatNumber(28.46, ".1f")).toBe("28.5");
    expect(formatNumber(0.2846, ".1%")).toBe("28.5%");
    expect(formatNumber(1234.5678, null)).toBe("1,234.57");
    expect(formatNumber(Number.NaN, ",d")).toBe("–");
  });

  it("adds the unit, singular for exactly one", () => {
    expect(formatWithUnit(120, { format: ",d", unit: "trials" })).toBe("120 trials");
    expect(formatWithUnit(1, { format: ",d", unit: "trials" })).toBe("1 trial");
    expect(formatWithUnit(5, { format: ",d", unit: null })).toBe("5");
  });
});

describe("pivot", () => {
  const periods = ["2020", "2021", "2022"];
  const groups = ["Phase 2", "Phase 3"];
  const long = periods.flatMap((year, i) =>
    groups.map((group, j) => datum({ start_year: year, group, trial_count: (i + 1) * 10 + j })),
  );

  it("makes one wide row per period with positional keys in domain order", () => {
    const result = pivot(long, { x: "start_year", y: "trial_count", series: { field: "group", domain: groups }, valueLabel: "Trials" });
    expect(result.series).toEqual([
      { key: "s0", label: "Phase 2" },
      { key: "s1", label: "Phase 3" },
    ]);
    expect(result.rows).toEqual([
      { x: "2020", s0: 10, s1: 11 },
      { x: "2021", s0: 20, s1: 21 },
      { x: "2022", s0: 30, s1: 31 },
    ]);
  });

  it("finds the long row again from a wide row and a series key", () => {
    const result = pivot(long, { x: "start_year", y: "trial_count", series: { field: "group", domain: groups }, valueLabel: "Trials" });
    for (const row of long) {
      const key = row.group === "Phase 2" ? "s0" : "s1";
      expect(result.find(String(row.start_year), key)).toBe(row);
    }
  });

  it("uses one series for rows without a series channel and keeps the category domain order", () => {
    const rows = [datum({ phase: "B", n: 2 }), datum({ phase: "A", n: 1 })];
    const result = pivot(rows, { x: "phase", y: "n", xDomain: ["A", "B", "C"], valueLabel: "Trials" });
    expect(result.rows).toEqual([
      { x: "A", s0: 1 },
      { x: "B", s0: 2 },
      { x: "C", s0: null },
    ]);
  });
});
