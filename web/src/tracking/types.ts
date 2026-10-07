// 和後端 app/spec.py 的 L1 schema 對應
export type PropertyType = "string" | "integer" | "number" | "boolean";

/** 行為時刻（和 api/app/spec.py 的 Moment 相同） */
export type Moment =
  | "page_load"
  | "product_detail_load"
  | "add_to_cart_click"
  | "add_to_cart_success"
  | "checkout_load"
  | "payment_success"
  | "order_complete_page_load"
  | "login_success";

/** 行為時刻的情境；L1 property 的 from（例如 "product.price"）就是取這裡的值 */
export type MomentContext = Partial<{
  page: { type: string };
  product: { id: string; price: number };
  item: { quantity: number };
  cart: { total: number; count: number };
  order: { id: string; coupon_code: string | null };
  login: { method: string };
}>;

export interface PropertySpec {
  type: PropertyType;
  description?: string;
  enum?: string[];
  min?: number;
  max?: number;
  from?: string;
  required?: boolean;
}

export interface EventSpec {
  description?: string;
  trigger?: string;
  fires_on: Moment[];
  properties?: Record<string, PropertySpec>;
}

export interface TrackingPlan {
  version: number;
  name?: string;
  status?: "current" | "proposal" | "retired";
  description?: string;
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

export interface TrackingPlanSummary {
  version: number;
  name: string;
  status: string;
  description: string;
  default: boolean;
}
