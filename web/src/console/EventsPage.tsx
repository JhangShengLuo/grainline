import { Link } from "react-router-dom";

import { planQuery, usePlanParam, useApi } from "./api";

interface LiveStats {
  received: number;
  with_warnings: number;
  last_seen: string | null;
  warnings: { message: string; count: number }[];
}

interface EventEntry {
  name: string;
  description: string;
  trigger: string;
  fires_on: { moment: string; text: string; caveat: string | null }[];
  properties: {
    name: string;
    type: string;
    required: boolean;
    enum: string[] | null;
    min: number | null;
    max: number | null;
    from: string | null;
    from_text: string;
    description: string;
    common: boolean;
  }[];
  used_by: { models: string[]; metrics: { name: string; label: string }[]; reports: { name: string; label: string }[] };
  live: LiveStats;
}

interface Catalog {
  tracking_plan: { version: number; name: string; status: string; description: string };
  shared_models: string[];
  events: EventEntry[];
  downstream_requests: { kind: string; event: string | null; property: string | null; message: string; impacted: string[] }[];
  unknown_events: ({ event_name: string } & LiveStats)[];
}

function typeText(p: EventEntry["properties"][number]): string {
  const extra = [p.enum && `可選 ${p.enum.join(" / ")}`, p.min !== null && `≥ ${p.min}`, p.max !== null && `≤ ${p.max}`].filter(Boolean);
  return extra.length ? `${p.type}（${extra.join("，")}）` : p.type;
}

export function EventsPage() {
  const [plan] = usePlanParam();
  const { data, error } = useApi<Catalog>(`/events${planQuery(plan)}`, 5000);
  if (error) return <p className="warning-text">{error.message}</p>;
  if (!data) return <p className="muted">載入埋點清單…</p>;
  const tp = data.tracking_plan;

  return (
    <>
      <h1>埋點清單</h1>
      <p className="muted">
        tracking plan v{tp.version} {tp.name}（{tp.status === "current" ? "現行" : tp.status === "proposal" ? "提案" : "停用"}）：{tp.description}
      </p>

      {data.downstream_requests.length > 0 && (
        <div className="callout">
          <p><b>下游需要、但這個版本還沒有埋的（{data.downstream_requests.length}）</b></p>
          <ul>
            {data.downstream_requests.map((r, i) => (
              <li key={i} className="small">
                {r.property ? <><code>{r.event}.{r.property}</code>：</> : <><code>{r.event}</code> 事件：</>}
                {r.message}
                {r.impacted.length > 0 && <span className="muted">（影響 {r.impacted.join("、")}）</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {data.unknown_events.length > 0 && (
        <div className="callout">
          <p><b>demo 商店送了、但 L1 沒有宣告的事件</b>：這些事件會被隔離，不會進任何報表。要用的話，先加進 tracking plan。</p>
          <ul>
            {data.unknown_events.map((u) => (
              <li key={u.event_name} className="small"><code>{u.event_name}</code>：收到 {u.received} 筆</li>
            ))}
          </ul>
        </div>
      )}

      <p className="muted small">
        {data.shared_models.join("、")} 會用到所有事件（切 session、合併身分），所以任何事件的增減或送出時機改變，都可能影響「造訪次數」和「人數」。
      </p>

      {data.events.map((e) => (
        <article key={e.name} className="finding event-card">
          <header>
            <code className="event-name">{e.name}</code>
            <span className="muted">{e.description}</span>
            {e.live.received > 0 && (
              <span className="subject">
                demo 商店收到 {e.live.received} 筆{e.live.with_warnings > 0 && `，${e.live.with_warnings} 筆不符 L1`}
              </span>
            )}
          </header>
          <p>
            <b>什麼時候送</b>　{e.fires_on.map((f) => f.text).join("、")}
            <span className="muted small">（{e.trigger}）</span>
          </p>
          {e.fires_on.filter((f) => f.caveat).map((f) => (
            <p key={f.moment} className="warning-text small">! {f.caveat}</p>
          ))}
          <div className="table-scroll">
            <table className="data compact">
              <thead><tr><th>欄位</th><th>型別</th><th>必填</th><th>值從哪裡來</th><th>說明</th></tr></thead>
              <tbody>
                {e.properties.map((p) => (
                  <tr key={p.name}>
                    <td><code>{p.name}</code>{p.common && <span className="tag neutral">共用</span>}</td>
                    <td>{typeText(p)}</td>
                    <td>{p.required ? "是" : "否"}</td>
                    <td>{p.from_text}{p.from && <span className="muted small"> <code>{p.from}</code></span>}</td>
                    <td className="small">{p.description}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="small">
            <b>下游用到</b>{"　"}
            {e.used_by.metrics.length === 0 ? (
              <span className="muted">目前沒有指標用到這個事件。</span>
            ) : (
              <>
                {e.used_by.metrics.map((m) => (
                  <Link key={m.name} to={{ pathname: `/console/metrics/${m.name}`, search: planQuery(plan) }} className="chip-code">{m.label}</Link>
                ))}
                {e.used_by.reports.length > 0 && <span className="muted">　報表：{e.used_by.reports.map((r) => r.label).join("、")}</span>}
              </>
            )}
          </p>
          {e.live.warnings.length > 0 && (
            <ul className="warnings">
              {e.live.warnings.map((w) => <li key={w.message}>⚠ {w.message}（{w.count} 筆）</li>)}
            </ul>
          )}
        </article>
      ))}
    </>
  );
}
