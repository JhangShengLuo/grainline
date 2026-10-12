import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { api } from "../api";
import { useTracking } from "../tracking/TrackingProvider";
import type { Moment } from "../tracking/types";

/** 行為時刻的白話（和 api/app/explain.py 相同） */
export const MOMENT_TEXT: Record<Moment, string> = {
  page_load: "頁面載入完成時",
  product_impression: "商品卡片出現在畫面上時",
  product_detail_load: "商品詳情載入完成時",
  add_to_cart_click: "按下「加入購物車」的當下",
  add_to_cart_success: "購物車 API 回傳成功後",
  checkout_load: "結帳頁載入完成時",
  payment_success: "付款成功、後端建立訂單時",
  order_complete_page_load: "付款成功頁載入時",
  login_success: "登入成功時",
};

interface EventInfo {
  name: string;
  fires_on: { moment: Moment; caveat: string | null }[];
  properties: { name: string; type: string; required: boolean; from_text: string; common: boolean }[];
  used_by: { metrics: { name: string; label: string }[] };
}

interface LibraryItem {
  label: string;
  missing: { kind: string; moment?: string; event?: string; what: string }[];
}

interface Insight {
  overlay: boolean;
  setOverlay: (on: boolean) => void;
  events: EventInfo[];
  /** 這個行為時刻在目前的設計下沒被記錄，會讓哪些指標算不出來或定義不同 */
  missing: Record<string, { metric: string; what: string }[]>;
  metricsOf: (eventName: string) => string[];
}

const InsightContext = createContext<Insight | null>(null);
const KEY = "grainline.shop.overlay";

export function InsightProvider({ children }: { children: ReactNode }) {
  const { planVersion } = useTracking();
  const [overlay, setOverlayState] = useState(() => {
    // 從會議模式點進來時（?overlay=1）直接打開
    if (new URLSearchParams(window.location.search).get("overlay") === "1") return true;
    try {
      return localStorage.getItem(KEY) === "1";
    } catch {
      return false;
    }
  });
  const [events, setEvents] = useState<EventInfo[]>([]);
  const [library, setLibrary] = useState<LibraryItem[]>([]);

  useEffect(() => {
    api<{ events: EventInfo[] }>(`/events?plan=${planVersion}`).then((b) => setEvents(b.events), () => setEvents([]));
    api<{ metrics: LibraryItem[] }>(`/library?plan=${planVersion}`).then((b) => setLibrary(b.metrics), () => setLibrary([]));
  }, [planVersion]);

  const setOverlay = useCallback((on: boolean) => {
    try {
      localStorage.setItem(KEY, on ? "1" : "0");
    } catch {
      // 存不了就只在這個分頁有效
    }
    setOverlayState(on);
  }, []);

  const value = useMemo<Insight>(() => {
    const missing: Insight["missing"] = {};
    for (const item of library) {
      for (const m of item.missing) {
        if (!m.moment) continue;
        const list = (missing[m.moment] ??= []);
        if (!list.some((x) => x.metric === item.label)) list.push({ metric: item.label, what: m.what });
      }
    }
    const byEvent = new Map(events.map((e) => [e.name, e.used_by.metrics.map((m) => m.label)]));
    return { overlay, setOverlay, events, missing, metricsOf: (name) => byEvent.get(name) ?? [] };
  }, [overlay, setOverlay, events, library]);

  return <InsightContext.Provider value={value}>{children}</InsightContext.Provider>;
}

export function useInsight(): Insight {
  const value = useContext(InsightContext);
  if (!value) throw new Error("useInsight 必須在 InsightProvider 裡使用");
  return value;
}

function MomentDetail({ moment }: { moment: Moment }) {
  const { events, missing } = useInsight();
  const { planVersion } = useTracking();
  const firing = events.filter((e) => e.fires_on.some((f) => f.moment === moment));
  return (
    <div className="tp-moment">
      <div className="tp-when">{MOMENT_TEXT[moment]}</div>
      {firing.length === 0 ? (
        <div className="tp-missing">
          v{planVersion} 的埋點設計不記錄這個動作
          {(missing[moment] ?? []).length > 0 && (
            <ul>{missing[moment].map((m) => <li key={m.metric}>{m.metric}：{m.what}</li>)}</ul>
          )}
        </div>
      ) : (
        firing.map((e) => {
          const caveat = e.fires_on.find((f) => f.moment === moment)?.caveat;
          return (
            <div key={e.name} className="tp-event">
              <code>{e.name}</code>
              <ul className="tp-props">
                {e.properties.filter((p) => !p.common).map((p) => (
                  <li key={p.name}>
                    <code>{p.name}</code> <span>{p.from_text}</span>
                    {!p.required && <span className="tp-optional">選填</span>}
                  </li>
                ))}
              </ul>
              {e.used_by.metrics.length > 0 ? (
                <div className="tp-metrics">→ {e.used_by.metrics.map((m) => m.label).join("、")}</div>
              ) : (
                <div className="tp-metrics muted">目前沒有指標用到</div>
              )}
              {caveat && <div className="tp-caveat">! {caveat}</div>}
              {(missing[moment] ?? []).length > 0 && (
                <ul className="tp-caveat">{missing[moment].map((m) => <li key={m.metric}>{m.metric}：{m.what}</li>)}</ul>
              )}
            </div>
          );
        })
      )}
    </div>
  );
}

function tracked(events: EventInfo[], moments: Moment[]): boolean {
  return moments.some((m) => events.some((e) => e.fires_on.some((f) => f.moment === m)));
}

/** 包住一個會送出埋點的元素：埋點透視打開時加上外框，滑鼠移上去看事件、欄位、下游指標 */
export function TrackPoint({
  moments,
  label,
  note,
  custom,
  block,
  children,
}: {
  moments: Moment[];
  label: string;
  note?: string;
  /** L1 沒有宣告、直接用 track() 送的事件 */
  custom?: string;
  block?: boolean;
  children: ReactNode;
}) {
  const { overlay, events } = useInsight();
  if (!overlay) return <>{children}</>;
  const ok = custom ? false : tracked(events, moments);
  return (
    <span className={`trackpoint ${ok ? "on" : "missing"} ${block ? "block" : ""}`}>
      {children}
      <span className="tp-popover" role="tooltip">
        <span className="tp-title">{label}</span>
        {custom ? (
          <span className="tp-missing">
            送出 <code>{custom}</code>，但這個事件沒有在 L1 宣告：會被隔離，不會進任何報表。要用的話先加進 tracking plan。
          </span>
        ) : (
          moments.map((m) => <MomentDetail key={m} moment={m} />)
        )}
        {note && <span className="tp-note">{note}</span>}
      </span>
    </span>
  );
}

/** 頁面層級的埋點（頁面載入），放在標題下面 */
export function PageMoments({ moments, label }: { moments: Moment[]; label: string }) {
  const { overlay } = useInsight();
  if (!overlay) return null;
  return (
    <TrackPoint moments={moments} label={label} block>
      <span className="tp-page">📄 {label}</span>
    </TrackPoint>
  );
}

/** 埋點透視打開時，每送出一個事件就在左下角提示「這會變成哪些數字」 */
export function MetricToasts() {
  const { overlay, metricsOf } = useInsight();
  const { tracker } = useTracking();
  const [toasts, setToasts] = useState<{ id: string; event: string; metrics: string[] }[]>([]);

  useEffect(() => {
    if (!overlay) return;
    const seen = new Set<string>();
    return tracker.subscribe((t) => {
      if (t.status !== "queued" || seen.has(t.event.event_id) || t.event.event_name === "product_impression") return;
      seen.add(t.event.event_id);
      const toast = { id: t.event.event_id, event: t.event.event_name, metrics: metricsOf(t.event.event_name) };
      setToasts((prev) => [...prev.slice(-3), toast]);
      setTimeout(() => setToasts((prev) => prev.filter((x) => x.id !== toast.id)), 3500);
    });
  }, [overlay, tracker, metricsOf]);

  if (!overlay || toasts.length === 0) return null;
  return (
    <div className="metric-toasts" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className="metric-toast">
          <code>{t.event}</code>
          <span>{t.metrics.length ? `→ ${t.metrics.join("、")}` : "→ 沒有指標用到（會被隔離或只是記錄）"}</span>
        </div>
      ))}
    </div>
  );
}

export function OverlayToggle() {
  const { overlay, setOverlay } = useInsight();
  return (
    <button
      type="button"
      className={`overlay-toggle ${overlay ? "active" : ""}`}
      aria-pressed={overlay}
      onClick={() => setOverlay(!overlay)}
      title="在頁面上直接看：哪裡有埋點、送什麼、會變成哪些數字"
    >
      {overlay ? "◉ 埋點透視：開" : "○ 埋點透視"}
    </button>
  );
}
