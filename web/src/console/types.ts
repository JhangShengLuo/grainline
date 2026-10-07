import type { Format } from "./api";

export interface Hop {
  layer: string;
  name: string;
  detail: string;
}

export interface ReportSummary {
  name: string;
  label: string;
  time_grain: string;
  dimensions: string[];
  metrics: string[];
  broken: string | null;
}

export interface ReportMetric {
  name: string;
  label: string;
  additivity: string;
  format: Format;
}

export interface ReportData {
  name: string;
  label: string;
  tracking_plan_version: number;
  time_grain: string;
  dimensions: string[];
  metrics: ReportMetric[];
  live_periods: string[];
  rows: Record<string, string | number | null>[];
}

export interface Finding {
  rule: "R1" | "R2" | "R3";
  severity: "error" | "warning";
  kind: string;
  subject: string;
  message: string;
  impacted: string[];
  evidence: ({ summary?: string; rows?: Record<string, unknown>[]; path?: Hop[] } & Record<string, unknown>) | null;
}

export interface ChecksData {
  tracking_plan_version: number;
  generated_events: number;
  summary: { error: number; warning: number };
  findings: Finding[];
}

export interface LineageField {
  role: string;
  column: string;
  type: string | null;
  broken: string | null;
  hops: Hop[];
}

export interface LineageNode {
  metric: string;
  label: string;
  type: "simple" | "ratio" | "rollup";
  broken: string | null;
  kind?: string;
  model?: string;
  measure?: { agg: string; column: string | null };
  filters?: { column: string; op: string; value: unknown }[];
  fields?: LineageField[];
  numerator?: LineageNode;
  denominator?: LineageNode;
  of?: LineageNode;
  from_grain?: string;
  agg?: string;
}

export interface DiffMetric {
  name: string;
  label: string;
  type: string;
  format: Format;
  base: { value: number | null; broken: string | null };
  target: { value: number | null; broken: string | null };
  delta: number | null;
  pct: number | null;
  changed: boolean;
  related_changes: string[];
}

export interface DiffData {
  base: { version: number; name: string; status: string };
  target: { version: number; name: string; status: string };
  plan_changes: { kind: string; event: string; property: string | null; message: string }[];
  metrics: DiffMetric[];
  reports: { name: string; label: string; base_broken: string | null; target_broken: string | null }[];
  findings: { added: Finding[]; resolved: Finding[] };
}
