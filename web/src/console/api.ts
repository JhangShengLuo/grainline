import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: unknown,
  ) {
    super(typeof detail === "string" ? detail : `HTTP ${status}`);
  }
}

async function fetchJson<T>(path: string): Promise<T> {
  const response = await fetch(`/api${path}`);
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, body?.detail ?? null);
  return body as T;
}

/** GET 一個 API；refreshMs 有值時定期重抓（例如看 demo 商店的點擊即時進報表） */
export function useApi<T>(path: string | null, refreshMs?: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(
    (quiet: boolean) => {
      if (!path) return;
      if (!quiet) setLoading(true);
      fetchJson<T>(path).then(
        (d) => {
          setData(d);
          setError(null);
          setLoading(false);
        },
        (e: Error) => {
          setError(e);
          setLoading(false);
        },
      );
    },
    [path],
  );

  useEffect(() => {
    setData(null);
    load(false);
    if (!refreshMs) return;
    const timer = setInterval(() => load(true), refreshMs);
    return () => clearInterval(timer);
  }, [load, refreshMs]);

  return { data, error, loading, reload: () => load(true) };
}

/** console 目前看的 tracking plan 版本放在網址 ?plan=，可以直接分享 */
export function usePlanParam(): [number | null, (version: number) => void] {
  const [params, setParams] = useSearchParams();
  const value = Number(params.get("plan"));
  const plan = Number.isInteger(value) && value > 0 ? value : null;
  const setPlan = (version: number) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("plan", String(version));
      return next;
    });
  return [plan, setPlan];
}

export const planQuery = (plan: number | null, prefix = "?") => (plan ? `${prefix}plan=${plan}` : "");

export type Format = "number" | "percent";

export function formatValue(value: number | null | undefined, format: Format = "number"): string {
  if (value === null || value === undefined) return "—";
  if (format === "percent") return `${(value * 100).toFixed(1)}%`;
  return value.toLocaleString("zh-TW", { maximumFractionDigits: Number.isInteger(value) ? 0 : 1 });
}

export function formatDelta(value: number | null, format: Format = "number"): string {
  if (value === null) return "—";
  if (value === 0) return "0";
  const sign = value > 0 ? "+" : "−";
  const abs = Math.abs(value);
  return format === "percent" ? `${sign}${(abs * 100).toFixed(1)} pt` : `${sign}${formatValue(abs)}`;
}

const GRAIN_DAYS: Record<string, number> = { day: 1, week: 7, month: 31 };

/**
 * 依時間排序的期間；不連續的地方插入 null，讓折線在那裡斷開，
 * 不會把 9/29 和 10/07（demo 商店的點擊）畫成相鄰的兩天。
 */
export function withGaps(periods: string[], grain: string): (string | null)[] {
  const step = (GRAIN_DAYS[grain] ?? 1) * 86_400_000;
  const sorted = [...periods].sort();
  return sorted.flatMap((p, i) =>
    i > 0 && Date.parse(p) - Date.parse(sorted[i - 1]) > step * 1.5 ? [null, p] : [p],
  );
}
