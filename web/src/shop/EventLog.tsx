import { useCallback, useEffect, useState } from "react";

import { api, type LiveEvent } from "../api";
import { useTracking } from "../tracking/TrackingProvider";
import type { TrackedEvent } from "../tracking/types";

const STATUS_LABEL = { queued: "排隊中", sent: "已送出", failed: "送出失敗，稍後重送" } as const;
const time = (iso: string) => new Date(iso.includes("T") ? iso : iso.replace(" ", "T")).toLocaleTimeString("zh-TW", { hour12: false });
const short = (id: string | null) => (id ? (id.length > 14 ? `${id.slice(0, 14)}…` : id) : "—");

function SentEvent({ tracked }: { tracked: TrackedEvent }) {
  const { plan } = useTracking();
  const { event } = tracked;
  const spec = plan.events[event.event_name];
  const warnings = [...new Set([...tracked.clientWarnings, ...tracked.serverWarnings])];
  return (
    <li className={`log-item ${warnings.length ? "has-warning" : ""}`}>
      <div className="log-head">
        <code className="event-name">{event.event_name}</code>
        <span className={`status ${tracked.status}`}>{STATUS_LABEL[tracked.status]}</span>
        <time>{time(event.timestamp)}</time>
      </div>
      <p className="trigger">{spec ? `L1 觸發時機：${spec.trigger || spec.description}` : "L1 沒有這個事件"}</p>
      <dl className="props">
        {Object.entries(event.properties).map(([k, v]) => (
          <div key={k}><dt>{k}</dt><dd>{JSON.stringify(v)}</dd></div>
        ))}
        {event.user_id && <div><dt>user_id</dt><dd>{event.user_id}</dd></div>}
      </dl>
      {warnings.length > 0 && (
        <ul className="warnings">{warnings.map((w) => <li key={w}>⚠ {w}</li>)}</ul>
      )}
    </li>
  );
}

function ParsedEvents() {
  const [rows, setRows] = useState<LiveEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(() => {
    api<LiveEvent[]>("/events/live?limit=30").then(
      (r) => { setRows(r); setError(null); },
      (e: Error) => setError(e.message),
    );
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 2000);
    return () => clearInterval(timer);
  }, [refresh]);

  async function clearAll() {
    if (!confirm("刪除所有 demo 商店送出的事件？模擬資料不受影響。")) return;
    await api("/events/live", { method: "DELETE" });
    refresh();
  }

  if (error) return <p className="warning-text">{error}</p>;
  if (!rows) return <p className="muted">載入中…</p>;
  return (
    <>
      <p className="muted small">
        伺服器收到後，L2 依 L1 驗證、切 session、做身分合併。登入後，同一裝置登入前的事件也會歸到同一個 person。
      </p>
      {rows.length === 0 ? (
        <p className="muted">還沒有事件。</p>
      ) : (
        <table className="parsed">
          <thead><tr><th>事件</th><th>person_id</th><th>session_id</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.event_id} className={r.quarantined ? "quarantined" : undefined}>
                <td><code>{r.event_name}</code>{r.quarantined && <span className="tag">已隔離</span>}</td>
                <td title={r.person_id ?? ""}>{short(r.person_id)}</td>
                <td title={r.session_id ?? ""}>{short(r.session_id?.split("-").pop() ?? null)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <button type="button" className="link danger" onClick={clearAll}>清除 demo 商店的事件</button>
    </>
  );
}

export function EventLog() {
  const { log, platform, tracker, userId } = useTracking();
  const [tab, setTab] = useState<"sent" | "parsed">("sent");
  return (
    <aside className="event-log" aria-label="埋點紀錄">
      <h2>埋點紀錄</h2>
      <p className="identity small">
        <span>platform <b>{platform}</b></span>
        <span title={tracker.anonymousId}>anonymous_id <b>{short(tracker.anonymousId)}</b></span>
        <span>user_id <b>{userId ?? "—（未登入）"}</b></span>
      </p>
      <div className="tabs" role="tablist">
        <button type="button" role="tab" aria-selected={tab === "sent"} onClick={() => setTab("sent")}>
          SDK 送出（{log.length}）
        </button>
        <button type="button" role="tab" aria-selected={tab === "parsed"} onClick={() => setTab("parsed")}>
          伺服器解析（L2）
        </button>
      </div>
      {tab === "sent" ? (
        log.length === 0 ? (
          <p className="muted">在左邊的商店逛逛，送出的每個事件都會出現在這裡。</p>
        ) : (
          <ul className="log">{log.map((t) => <SentEvent key={t.event.event_id} tracked={t} />)}</ul>
        )
      ) : (
        <ParsedEvents />
      )}
    </aside>
  );
}
