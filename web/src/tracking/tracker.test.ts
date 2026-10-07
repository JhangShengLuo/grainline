import { describe, expect, it, vi } from "vitest";

import { Tracker, type KeyValueStore, type Transport } from "./tracker";
import type { RawEvent, TrackingPlan } from "./types";
import { validateEvent } from "./validate";

const plan: TrackingPlan = {
  version: 1,
  common_properties: { platform: { type: "string", enum: ["web", "app"], from: "session.platform" } },
  events: {
    page_view: {
      fires_on: ["page_load"],
      properties: {
        page_type: { type: "string", enum: ["home", "product"], from: "page.type" },
        product_id: { type: "string", from: "product.id", required: false },
      },
    },
    add_to_cart: {
      fires_on: ["add_to_cart_success"],
      properties: {
        product_id: { type: "string", from: "product.id" },
        price: { type: "number", min: 0, from: "product.price" },
        quantity: { type: "integer", min: 1, from: "item.quantity" },
      },
    },
    login: { fires_on: ["login_success"], properties: { method: { type: "string", from: "login.method" } } },
  },
};

function memoryStore(): KeyValueStore {
  const data = new Map<string, string>();
  return {
    getItem: (k) => data.get(k) ?? null,
    setItem: (k, v) => void data.set(k, v),
    removeItem: (k) => void data.delete(k),
  };
}

function setup(transport?: Partial<Transport>, storage = memoryStore()) {
  const sent: RawEvent[][] = [];
  let n = 0;
  const tracker = new Tracker({
    platform: "web",
    plan,
    storage,
    flushIntervalMs: 60_000,
    newId: () => `id-${++n}`,
    now: () => new Date("2026-10-07T02:00:00Z"),
    transport: {
      send: async (events) => {
        sent.push(events);
        return { accepted: events.length, duplicates: 0, warnings: [] };
      },
      ...transport,
    },
  });
  return { tracker, sent, storage };
}

describe("validateEvent", () => {
  it("accepts an event matching the plan", () => {
    expect(validateEvent(plan, "add_to_cart", { platform: "web", product_id: "p1", price: 10, quantity: 1 })).toEqual([]);
  });

  it.each([
    ["wishlist_add", {}, "沒有在 L1 宣告"],
    ["add_to_cart", { platform: "web", product_id: "p1", price: 10 }, "缺少 property quantity"],
    ["add_to_cart", { platform: "web", product_id: "p1", price: 10, quantity: 1.5 }, "quantity 應為 integer"],
    ["add_to_cart", { platform: "web", product_id: "p1", price: -1, quantity: 1 }, "小於 L1 的下限"],
    ["page_view", { platform: "web", page_type: "search" }, "不在 L1 enum"],
    ["page_view", { platform: "web", page_type: "home", ref: "ad" }, "ref 沒有在 L1 宣告"],
  ])("warns about %s %j", (name, props, expected) => {
    expect(validateEvent(plan, name, props).join("\n")).toContain(expected);
  });
});

describe("Tracker.moment", () => {
  it("sends the events the plan fires on that moment, filling properties from context", async () => {
    const { tracker, sent } = setup();
    tracker.moment("add_to_cart_success", { product: { id: "p1", price: 399 }, item: { quantity: 2 } });
    await tracker.flush();
    expect(sent[0].map((e) => [e.event_name, e.properties])).toEqual([
      ["add_to_cart", { platform: "web", product_id: "p1", price: 399, quantity: 2 }],
    ]);
  });

  it("sends nothing for a moment the plan does not track", () => {
    const { tracker } = setup();
    expect(tracker.moment("add_to_cart_click", { product: { id: "p1", price: 399 } })).toEqual([]);
  });

  it("leaves out optional properties missing from context without warning", () => {
    const { tracker } = setup();
    const [tracked] = tracker.moment("page_load", { page: { type: "home" } });
    expect(tracked.event.properties).toEqual({ platform: "web", page_type: "home" });
    expect(tracked.clientWarnings).toEqual([]);
  });

  it("warns when a required property is missing from context", () => {
    const { tracker } = setup();
    const [tracked] = tracker.moment("add_to_cart_success", { product: { id: "p1", price: 399 } });
    expect(tracked.clientWarnings.join()).toContain("缺少 property quantity");
  });
});

describe("Tracker", () => {
  it("builds the envelope and adds the platform property", async () => {
    const { tracker, sent } = setup();
    tracker.track("page_view", { page_type: "home" });
    await tracker.flush();
    expect(sent[0][0]).toEqual({
      event_id: "id-2",
      event_name: "page_view",
      timestamp: "2026-10-07T02:00:00.000Z",
      anonymous_id: "anon-id-1",
      user_id: null,
      tracking_plan_version: 1,
      properties: { platform: "web", page_type: "home" },
    });
  });

  it("keeps the anonymous id across instances (same device)", () => {
    const storage = memoryStore();
    const first = setup(undefined, storage).tracker.anonymousId;
    expect(setup(undefined, storage).tracker.anonymousId).toBe(first);
  });

  it("adds user_id after identify and drops it after logout", async () => {
    const { tracker, sent } = setup();
    tracker.track("page_view", { page_type: "home" });
    tracker.identify("demo-amy");
    tracker.track("login", { method: "email" });
    tracker.logout();
    tracker.track("page_view", { page_type: "home" });
    await tracker.flush();
    expect(sent[0].map((e) => e.user_id)).toEqual([null, "demo-amy", null]);
  });

  it("still sends events that break the plan, with warnings attached", async () => {
    const { tracker, sent } = setup();
    const tracked = tracker.track("wishlist_add", { product_id: "p1" });
    expect(tracked.clientWarnings[0]).toContain("沒有在 L1 宣告");
    await tracker.flush();
    expect(sent[0]).toHaveLength(1);
  });

  it("records server warnings and notifies listeners", async () => {
    const { tracker } = setup({
      send: async (events) => ({
        accepted: 1,
        duplicates: 0,
        warnings: [{ event_id: events[0].event_id, event_name: "page_view", messages: ["伺服器說不對"] }],
      }),
    });
    const statuses: string[] = [];
    tracker.subscribe((t) => statuses.push(t.status));
    const tracked = tracker.track("page_view", { page_type: "home" });
    await tracker.flush();
    expect(statuses).toEqual(["queued", "sent"]);
    expect(tracked.serverWarnings).toEqual(["伺服器說不對"]);
  });

  it("re-queues a failed batch and resends the same event ids", async () => {
    const send = vi
      .fn<Transport["send"]>()
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValue({ accepted: 1, duplicates: 0, warnings: [] });
    const { tracker } = setup({ send });
    const tracked = tracker.track("page_view", { page_type: "home" });
    await tracker.flush();
    expect(tracked.status).toBe("failed");
    await tracker.flush();
    expect(tracked.status).toBe("sent");
    expect(send.mock.calls[1][0][0].event_id).toBe(send.mock.calls[0][0][0].event_id);
  });

  it("flushes immediately when the batch is full", async () => {
    const { tracker, sent } = setup();
    for (let i = 0; i < 20; i++) tracker.track("page_view", { page_type: "home" });
    await vi.waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toHaveLength(20);
  });
});
