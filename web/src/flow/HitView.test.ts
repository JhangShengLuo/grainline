import { describe, expect, it } from "vitest";

import { requiredFor } from "./HitView";

describe("requiredFor", () => {
  it("only returns params this event must carry", () => {
    const missing = [{ event: "purchase", param: "order" }, { event: "page_view", param: "utm_campaign" }];
    expect(requiredFor("purchase", missing)).toEqual(["order"]);
    expect(requiredFor("list_view", missing)).toEqual([]);
  });
});
