import { describe, expect, it } from "vitest";

import { campaignText, count, money, percent } from "./api";

describe("formatting", () => {
  it("formats money, counts and percents", () => {
    expect(money(1234.6)).toBe("NT$ 1,235");
    expect(count(3)).toBe("3");
    expect(count(2.333)).toBe("2.3");
    expect(percent(0.474)).toBe("47%");
  });
  it("names campaigns", () => {
    expect(campaignText("xmas_fb")).toBe("FB 廣告");
    expect(campaignText(null)).toBe("直接進站");
  });
});
