import { planQuery, usePlanParam, useApi } from "./api";
import type { Hop } from "./types";

interface Column {
  name: string;
  from: string | null;
  agg: string | null;
  type: string | null;
  broken: string | null;
  grain: boolean;
  origin: Hop | null;
}

interface Model {
  name: string;
  kind: "fact" | "entity";
  description: string;
  source: string;
  grain: string[];
  rows: number | null;
  broken: string | null;
  columns: Column[];
  grain_check: { ok: boolean; summary: string } | null;
  metrics: string[];
  sql: string | null;
}

interface Catalog {
  staging: {
    session_timeout_minutes: number;
    identity: string;
    identity_text: string;
    session_text: string;
    unknown_events: number;
    sql: Record<string, string>;
  };
  models: Model[];
}

export function ModelsPage() {
  const [plan] = usePlanParam();
  const { data, error } = useApi<Catalog>(`/models${planQuery(plan)}`, 10000);
  if (error) return <p className="warning-text">{error.message}</p>;
  if (!data) return <p className="muted">載入資料模型…</p>;
  const st = data.staging;

  return (
    <>
      <h1>資料模型</h1>
      <p className="muted">L2 與 L3 都是 view，建在不可變的 raw_events 上，查詢時才計算；改了 YAML 只要重新編譯，不需要 backfill。</p>

      <section className="finding">
        <header><span className="rule">L2 解析</span><code className="subject">l2_staging.yaml</code></header>
        <p>{st.identity_text} <span className="muted small">（identity: {st.identity}）</span></p>
        <p>{st.session_text} <span className="muted small">（session_timeout_minutes: {st.session_timeout_minutes}）</span></p>
        <p className="small">L1 沒有宣告的事件會進 <code>stg_unknown_events</code>，目前 {st.unknown_events.toLocaleString()} 筆。</p>
        {Object.entries(st.sql).map(([name, sql]) => (
          <details key={name}><summary>{name} 的 SQL</summary><pre className="sql">{sql}</pre></details>
        ))}
      </section>

      {data.models.map((m) => (
        <section key={m.name} className={`finding ${m.broken || m.grain_check?.ok === false ? "error" : ""}`}>
          <header>
            <code className="event-name">{m.name}</code>
            <span className="tag neutral">{m.kind === "fact" ? "fact：一列一個事件" : "entity：依 grain 分組"}</span>
            <span className="muted">{m.description}</span>
            {m.rows !== null && <span className="subject">{m.rows.toLocaleString()} 列</span>}
          </header>
          <p className="small meta">
            <span>來源 <code>{m.source === "*" ? "所有事件（stg_events）" : `stg_${m.source}`}</code></span>
            <span>grain <code>{m.grain.join(", ")}</code></span>
            {m.metrics.length > 0 && <span className="muted">指標：{m.metrics.join("、")}</span>}
          </p>
          {m.broken && <p className="warning-text small">✕ 無法建立：{m.broken}</p>}
          {m.grain_check && (
            <p className={`small ${m.grain_check.ok ? "success" : "warning-text"}`}>
              {m.grain_check.ok ? "✓" : "✕"} {m.grain_check.summary}
            </p>
          )}
          <div className="table-scroll">
            <table className="data compact">
              <thead><tr><th>欄位</th><th>型別</th><th>算法</th><th>追到 L1</th></tr></thead>
              <tbody>
                {m.columns.map((c) => (
                  <tr key={c.name}>
                    <td><code>{c.name}</code>{c.grain && <span className="tag neutral">grain</span>}</td>
                    <td>{c.type ?? "—"}</td>
                    <td><code>{c.agg ? `${c.agg}(${c.from ?? "*"})` : c.from}</code></td>
                    <td className="small">
                      {c.broken ? <span className="warning-text">✕ {c.broken}</span>
                        : c.origin ? <><span className="muted">{c.origin.layer}</span> <code>{c.origin.name}</code></>
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {m.sql && <details><summary>編譯後的 SQL</summary><pre className="sql">{m.sql}</pre></details>}
        </section>
      ))}
    </>
  );
}
