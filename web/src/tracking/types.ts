// 和後端 app/spec.py 的 L1 schema 對應
export type PropertyType = "string" | "integer" | "number" | "boolean";

export interface PropertySpec {
  type: PropertyType;
  description?: string;
  enum?: string[];
  min?: number;
  max?: number;
}

export interface EventSpec {
  description?: string;
  trigger?: string;
  properties?: Record<string, PropertySpec>;
}

export interface TrackingPlan {
  version: number;
  common_properties?: Record<string, PropertySpec>;
  events: Record<string, EventSpec>;
}

export type Properties = Record<string, string | number | boolean | null>;

/** 送到 /ingest 的原始事件（信封欄位 + properties） */
export interface RawEvent {
  event_id: string;
  event_name: string;
  timestamp: string;
  anonymous_id: string;
  user_id: string | null;
  tracking_plan_version: number;
  properties: Properties;
}

export interface IngestResponse {
  accepted: number;
  duplicates: number;
  warnings: { event_id: string; event_name: string; messages: string[] }[];
  note?: string;
}

export type EventStatus = "queued" | "sent" | "failed";

/** SDK 送出的每個事件與它的狀態，給埋點紀錄面板顯示 */
export interface TrackedEvent {
  event: RawEvent;
  status: EventStatus;
  clientWarnings: string[];
  serverWarnings: string[];
}
