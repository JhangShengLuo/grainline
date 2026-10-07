import type { IngestResponse, RawEvent } from "./types";
import type { Transport } from "./tracker";

export function fetchTransport(endpoint = "/api/ingest"): Transport {
  return {
    async send(events: RawEvent[]): Promise<IngestResponse> {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ events }),
      });
      if (!response.ok) throw new Error(`ingest 失敗：HTTP ${response.status}`);
      return response.json();
    },
    beacon(events: RawEvent[]): boolean {
      const body = new Blob([JSON.stringify({ events })], { type: "application/json" });
      return navigator.sendBeacon(endpoint, body);
    },
  };
}
