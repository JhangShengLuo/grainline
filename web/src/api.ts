export interface Product {
  id: string;
  name: string;
  category: string;
  price: number;
}

/** GET /events/live：demo 商店送出的事件，以及 L2 怎麼解析它 */
export interface LiveEvent {
  event_id: string;
  event_name: string;
  timestamp: string;
  anonymous_id: string;
  user_id: string | null;
  person_id: string | null;
  session_id: string | null;
  quarantined: boolean;
  properties: Record<string, unknown>;
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, init);
  if (!response.ok) throw new Error(`${init?.method ?? "GET"} ${path} 失敗：HTTP ${response.status}`);
  return response.json();
}

export const formatPrice = (value: number) => `NT$ ${value.toLocaleString("zh-TW")}`;
