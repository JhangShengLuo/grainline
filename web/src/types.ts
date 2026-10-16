export type Model = "last" | "first" | "linear";

export interface Plan {
  version: number;
  name: string;
  events: Record<string, { params: string[]; when: string }>;
}

export interface Product {
  sku: string;
  name: string;
  cat: string;
  price: number;
}

export interface Requirement {
  event: string;
  param: string;
  why: string;
}

export interface Question {
  id: string;
  title: string;
  ask: string;
  phrasings: string[];
  stance: string;
  grain: string;
  attribution: { default: Model; options: Model[] } | null;
  fanout: string;
  requires: Requirement[];
}

export interface BudgetRow {
  campaign: string;
  revenue: number;
  orders: number;
  share: number;
  last: number;
  first: number;
  linear: number;
}

export interface AssistRow {
  campaign: string;
  touched_orders: number;
  last_orders: number;
  assist_orders: number;
  linear_revenue: number;
  last_revenue: number;
}

export interface SkuRow {
  sku: string;
  name: string;
  cat: string;
  price: number;
  impressions: number;
  views: number;
  adds: number;
  orders: number;
  units: number;
  revenue: number;
  view_to_order: number;
  flag: boolean;
}

export interface Fanout {
  kind: string;
  label: string;
  wrong_sql: string;
  right_sql: string;
  wrong: Record<string, number>;
  right: Record<string, number>;
  metric: "orders" | "units";
  ratio: number;
  per_key: Record<string, { key: string; orders?: number; revenue?: number; units?: number }>;
  example: { key: string; lines: number; touches: number; rows_after: number } | null;
}

export interface Missing {
  event: string;
  param: string;
  why: string;
  hits: number;
  present: number;
  rate: number;
  status: "ok" | "partial" | "missing";
  in_plan: boolean;
  impact: { label: string; value: number; share: number } | null;
}

export interface QuestionResult {
  question: Question;
  plan: number;
  attribution: Model | null;
  dashboard: {
    total?: number;
    rows: (BudgetRow | AssistRow | SkuRow)[];
    categories?: { cat: string; impressions: number; views: number; adds: number; orders: number; units: number; revenue: number }[];
  };
  fanout: Fanout;
  missing: Missing[];
}

export interface PersonSummary {
  person_id: string;
  label: string;
  sessions: number;
  touches: number;
  orders: number;
}

export interface Table {
  model: string;
  title: string;
  grain: string;
  rows: Record<string, string | number | boolean>[];
}

export interface PageInfo {
  kind: "home" | "campaign" | "category" | "product" | "checkout" | "complete";
  sku?: string;
  cat?: string;
  items?: { sku: string; cat: string; price: number; pos: number }[];
}

export interface Step {
  hit_id: number;
  ts: string;
  sid: string;
  ev: string;
  url: string;
  params: { key: string; value: string }[];
  page: PageInfo;
  session_start: boolean;
  campaign: string | null;
  tables: Table[];
  order_linked?: boolean;
}

export interface Attribution {
  campaign: string;
  touch_id?: string;
  weight: number;
  revenue: number;
}

export interface Order {
  order_id: string;
  total: number;
  created_at: string;
  linked_by: "uid" | "order" | null;
  after_hit_id: number | null;
  tables: Table[];
  attribution: Partial<Record<Model, Attribution[]>>;
}

export interface Journey {
  person_id: string;
  aids: string[];
  steps: Step[];
  orders: Order[];
  touches: Record<string, string>[];
}

/** 放大頁面上的操作，會變成「你的旅程」的下一步 */
export type ShopAction =
  | { action: "land"; campaign: string | null }
  | { action: "list" }
  | { action: "product"; sku: string }
  | { action: "add"; sku: string }
  | { action: "purchase" };
