export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, init);
  if (!response.ok) throw new Error(`${init?.method ?? "GET"} ${path} 失敗：HTTP ${response.status}`);
  return response.json();
}

export const formatPrice = (value: number) => `NT$ ${Math.round(value).toLocaleString("zh-TW")}`;
