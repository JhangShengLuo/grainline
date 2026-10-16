import { useEffect, useState } from "react";

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, init);
  if (!response.ok) throw new Error(`${init?.method ?? "GET"} ${path} 失敗：HTTP ${response.status}`);
  const type = response.headers.get("content-type") ?? "";
  return (type.includes("json") ? response.json() : response.text()) as Promise<T>;
}

/** 依 path 取資料；path 為 null 時不取。reload 改變時重取。 */
export function useApi<T>(path: string | null, reload = 0): { data: T | null; error: string | null; loading: boolean } {
  const [state, setState] = useState<{ data: T | null; error: string | null; loading: boolean }>({
    data: null,
    error: null,
    loading: path !== null,
  });
  useEffect(() => {
    if (path === null) return;
    let alive = true;
    setState((s) => ({ ...s, loading: true }));
    api<T>(path)
      .then((data) => alive && setState({ data, error: null, loading: false }))
      .catch((e: Error) => alive && setState({ data: null, error: e.message, loading: false }));
    return () => {
      alive = false;
    };
  }, [path, reload]);
  return state;
}

export const money = (value: number) => `NT$ ${Math.round(value).toLocaleString("zh-TW")}`;
export const count = (value: number) => (Number.isInteger(value) ? value : Math.round(value * 10) / 10).toLocaleString("zh-TW");
export const percent = (value: number) => `${Math.round(value * 100)}%`;

export const CAMPAIGN_TEXT: Record<string, string> = {
  xmas_fb: "FB 廣告",
  xmas_line: "LINE 推播",
  xmas_gg: "Google 搜尋廣告",
  xmas_edm: "EDM",
  "(直接進站)": "直接進站",
  "(無法歸因)": "無法歸因",
};
export const campaignText = (c: string | null) => (c ? (CAMPAIGN_TEXT[c] ?? c) : "直接進站");

export const MODEL_TEXT = { last: "最後點擊", first: "首次點擊", linear: "線性" } as const;

export const EVENT_TEXT: Record<string, string> = {
  page_view: "看頁面",
  list_view: "看列表",
  product_view: "看商品",
  add_to_cart: "加入購物車",
  purchase: "完成購買",
};
