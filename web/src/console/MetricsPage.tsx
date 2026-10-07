import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { planQuery, usePlanParam, useApi, type Format } from "./api";

interface MetricItem {
  name: string;
  label: string;
  type: string;
  summary: string;
  broken: string | null;
  problems: number;
  reports: string[];
}

interface Step {
  layer: string;
  title: string;
  text: string;
  detail?: string;
  broken?: boolean | null;
}

interface Explanation {
  name: string;
  label: string;
  description: string;
  type: string;
  format: Format;
  summary: string;
  formula?: string;
  additivity: string;
  additivity_text: string;
  broken: string | null;
  tracking_plan_version: number;
  steps?: Step[];
  parts?: { role: string; name: string; label: string; steps: Step[] }[];
  caveats: { level: "info" | "warning" | "error"; text: string; rule?: string }[];
  example: {
    person_id: string;
    period: string;
    grain: string;
    summary: string;
    candidates: number;
    index: number;
    events: { time: string; event_name: string; platform: string; session: string; properties: Record<string, unknown>; marks: string[] }[];
  } | null;
}

const LEVEL = {
  info: { icon: "ℹ", label: "說明" },
  warning: { icon: "!", label: "注意" },
  error: { icon: "✕", label: "問題" },
} as const;

export function MetricsPage() {
  const [plan] = usePlanParam();
  const { data, error } = useApi<MetricItem[]>(`/metrics${planQuery(plan)}`);
  if (error) return <p className="warning-text">{error.message}</p>;
  if (!data) return <p className="muted">載入指標…</p>;
  return (
    <>
      <h1>指標說明</h1>
      <p className="muted">每個指標是由哪些使用者行為、經過哪些步驟算出來的。點進去可以看到白話的計算步驟、要注意的地方，以及一個實際的例子。</p>
      <ul className="metric-grid">
        {data.map((m) => (
          <li key={m.name}>
            <Link to={{ pathname: `/console/metrics/${m.name}`, search: planQuery(plan) }} className="card metric-card">
              <div className="metric-card-head">
                <b>{m.label}</b>
                {m.broken ? <span className="tag">算不出來</span> : m.problems > 0 && <span className="tag">{m.problems} 個要注意</span>}
              </div>
              <p className="small">{m.summary}</p>
              <p className="muted small">{m.reports.length ? `用在 ${m.reports.length} 張報表` : "目前沒有報表使用"}</p>
            </Link>
          </li>
        ))}
      </ul>
    </>
  );
}

function Steps({ steps }: { steps: Step[] }) {
  return (
    <ol className="steps">
      {steps.map((s, i) => (
        <li key={i} className={s.broken ? "broken" : undefined}>
          <span className="step-title"><span className="layer">{s.layer}</span>{s.title}</span>
          <span>
            {s.text}
            {s.detail && <span className="muted small">（L1 寫的觸發時機：{s.detail}）</span>}
          </span>
        </li>
      ))}
    </ol>
  );
}

export function MetricDetailPage() {
  const { name = "" } = useParams();
  const [plan] = usePlanParam();
  const [example, setExample] = useState(0);
  const { data, error } = useApi<Explanation>(`/metrics/${name}/explain?example=${example}${planQuery(plan, "&")}`);

  if (error) return <p className="warning-text">{error.message}</p>;
  if (!data) return <p className="muted">整理說明…</p>;
  return (
    <article className="explain">
      <p className="small"><Link to={{ pathname: "/console/metrics", search: planQuery(plan) }}>← 所有指標</Link></p>
      <h1>{data.label} <code className="muted small">{data.name}</code></h1>
      <p className="lead">{data.summary}</p>
      {data.description && <p className="muted">{data.description}</p>}
      <p className="small"><span className="tag neutral">{data.additivity_text}</span> 依 tracking plan v{data.tracking_plan_version} 計算</p>

      {data.broken && (
        <div className="callout"><p><b>這個指標在 tracking plan v{data.tracking_plan_version} 下算不出來。</b></p><p className="small">{data.broken}</p></div>
      )}

      <section>
        <h2>怎麼算出來的</h2>
        {data.formula && <p>{data.formula}</p>}
        {data.steps && <Steps steps={data.steps} />}
        {data.parts && (
          <div className={data.parts.length > 1 ? "parts" : undefined}>
            {data.parts.map((p) => (
              <div key={p.role}>
                <h3>{p.role}：<Link to={{ pathname: `/console/metrics/${p.name}`, search: planQuery(plan) }}>{p.label}</Link></h3>
                <Steps steps={p.steps} />
              </div>
            ))}
          </div>
        )}
      </section>

      {data.caveats.length > 0 && (
        <section>
          <h2>要注意</h2>
          <ul className="caveats">
            {data.caveats.map((c, i) => (
              <li key={i} className={c.level}>
                <span className={`severity ${c.level}`}>{LEVEL[c.level].icon} {LEVEL[c.level].label}</span>
                <span>{c.text}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {data.example && (
        <section>
          <div className="report-head">
            <h2>舉個例子</h2>
            <button type="button" onClick={() => setExample(data.example!.index + 1)}>
              換一個例子（{data.example.index + 1} / {data.example.candidates}）
            </button>
          </div>
          <p className="evidence">{data.example.summary}</p>
          <p className="muted small">
            這是模擬使用者 <code>{data.example.person_id}</code> 在{data.example.grain === "week" ? ` ${data.example.period} 這一週` : ` ${data.example.period}`}被記錄下來的所有事件。標示的列會被算進這個指標。
          </p>
          <div className="table-scroll">
            <table className="data compact timeline">
              <thead><tr><th>時間</th><th>事件</th><th>平台</th><th>造訪</th><th>內容</th><th>算進</th></tr></thead>
              <tbody>
                {data.example.events.map((e, i) => (
                  <tr key={i} className={e.marks.length ? "marked" : undefined}>
                    <td>{e.time}</td>
                    <td><code>{e.event_name}</code></td>
                    <td>{e.platform}</td>
                    <td>#{e.session}</td>
                    <td className="small">{Object.entries(e.properties).map(([k, v]) => `${k}=${v}`).join("　")}</td>
                    <td>{e.marks.map((m) => <span key={m} className="tag mark">{m}</span>)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </article>
  );
}
