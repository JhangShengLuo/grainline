import { describe, expect, it } from "vitest";

import { formatDelta, formatValue, withGaps } from "./api";

describe("withGaps", () => {
  it("breaks the line between non-consecutive periods", () => {
    expect(withGaps(["2026-09-29", "2026-09-28", "2026-10-07"], "day")).toEqual(["2026-09-28", "2026-09-29", null, "2026-10-07"]);
  });

  it("treats consecutive weeks as continuous", () => {
    expect(withGaps(["2026-08-31", "2026-09-07"], "week")).toEqual(["2026-08-31", "2026-09-07"]);
  });
});

describe("formatting", () => {
  it("formats rates as percent and deltas as points", () => {
    expect(formatValue(0.3385, "percent")).toBe("33.9%");
    expect(formatDelta(-0.0132, "percent")).toBe("−1.3 pt");
    expect(formatDelta(439)).toBe("+439");
    expect(formatValue(null)).toBe("—");
  });
});
