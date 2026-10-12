import { planQuery, usePlanParam, useApi } from "./api";

interface Summary {
  tracking_plan_version: number;
  standard: { name: string; version: string; commit: string };
  valid: boolean;
  errors: string[];
  counts: { datasets: number; relationships: number; metrics: number };
  not_exported: { metric: string; reason: string }[];
  metrics: { name: string; expression: string; grainline: { label: string; additivity: string; findings?: unknown[] } }[];
  cube: { ok: boolean; files: string[]; notes: string[]; error: string | null };
}

const MAPPING: [string, string, string][] = [
  ["L3 model（表）", "dataset", "grain → primary_key；欄位含型別、是否為時間維度"],
  ["lookup（查表）", "relationship", "多對一，用 by 對上被查的表的 grain"],
  ["L4 指標", "metric", "ANSI SQL 聚合運算式；篩選條件轉成 CASE WHEN，比率轉成相除"],
  ["可加性、grain 合約、欄位追到 L1 的血緣", "custom_extensions（GRAINLINE）", "標準沒有的欄位，放在延伸欄位不遺失"],
  ["L1 埋點合約、R4 業務規則與對帳、L5 決策劇本", "模型層的 custom_extensions", "其他工具不需要懂，也不會因此壞掉"],
];

export function ExportPage() {
  const [plan] = usePlanParam();
  const { data, error } = useApi<Summary>(`/export/summary${planQuery(plan)}`);
  if (error) return <p className="warning-text">{error.message}</p>;
  if (!data) return <p className="muted">匯出並用官方 schema 驗證中…</p>;
  const q = planQuery(plan);

  return (
    <article className="decision">
      <h1>語意層標準匯出</h1>
      <p className="lead">
        Grainline 的 YAML 是作者格式；對外匯出成 <b>{data.standard.name}</b>（原 Open Semantic Interchange，OSI）——Snowflake、dbt Labs、Salesforce 等發起、
        Apache 孵化中的開放標準，dbt 的 MetricFlow 可以直接讀。Cube 則用 Apache Ossie 官方的轉換器轉出。
      </p>

      <div className="tiles">
        <div className={`tile ${data.valid ? "primary-tile" : ""}`}>
          <div className="tile-value">{data.valid ? "✓ 通過" : "✕ 未通過"}</div>
          <div className="tile-label">官方 JSON Schema 驗證</div>
          <div className="muted small">{data.standard.name} {data.standard.version}（apache/ossie@{data.standard.commit.slice(0, 7)}，草案版本）</div>
        </div>
        <div className="tile">
          <div className="tile-value">{data.counts.datasets} / {data.counts.relationships} / {data.counts.metrics}</div>
          <div className="tile-label">datasets / relationships / metrics</div>
          <div className="muted small">tracking plan v{data.tracking_plan_version}</div>
        </div>
        <div className="tile">
          <div className="tile-value">{data.cube.ok ? `${data.cube.files.length} 個檔案` : "轉換失敗"}</div>
          <div className="tile-label">Cube 資料模型</div>
          <div className="muted small">{data.cube.ok ? "cubes 加上一個 view" : data.cube.error}</div>
        </div>
      </div>
      {data.errors.length > 0 && <ul className="warning-text small">{data.errors.map((e) => <li key={e}>{e}</li>)}</ul>}

      <div className="actions">
        <a className="button primary" href={`/api/export/osi.yaml${q}`}>下載 Apache Ossie YAML</a>
        <a className="button" href={`/api/export/cube.zip${q}`}>下載 Cube 資料模型（zip）</a>
      </div>

      <section>
        <h2>怎麼對應</h2>
        <div className="table-scroll">
          <table className="data">
            <thead><tr><th>Grainline</th><th>Apache Ossie</th><th>說明</th></tr></thead>
            <tbody>{MAPPING.map(([a, b, c]) => <tr key={a}><td>{a}</td><td><code>{b}</code></td><td className="small">{c}</td></tr>)}</tbody>
          </table>
        </div>
      </section>

      <section>
        <h2>標準表達不了、所以沒有匯出的（{data.not_exported.length}）</h2>
        {data.not_exported.length === 0 ? <p className="muted">沒有。</p> : (
          <ul className="small">{data.not_exported.map((n) => <li key={n.metric}><code>{n.metric}</code>：{n.reason}</li>)}</ul>
        )}
      </section>

      {data.cube.ok && data.cube.notes.length > 0 && (
        <section>
          <h2>轉成 Cube 時要注意（{data.cube.notes.length}）</h2>
          <p className="muted small">
            跨資料表的比率（例如轉換率：後端買家 ÷ 前端訪客）在 Cube 裡要有 join 路徑；Grainline 是在每個期間分別算出分子、分母再相除，所以不需要 join。
          </p>
          <ul className="small">{data.cube.notes.map((n) => <li key={n}>{n}</li>)}</ul>
        </section>
      )}

      <section>
        <h2>指標的標準運算式</h2>
        <div className="table-scroll">
          <table className="data compact">
            <thead><tr><th>指標</th><th>ANSI SQL</th><th>可加性</th></tr></thead>
            <tbody>
              {data.metrics.map((m) => (
                <tr key={m.name}>
                  <td>{m.grainline.label} <code className="muted small">{m.name}</code></td>
                  <td><code className="small">{m.expression}</code></td>
                  <td className="small">{m.grainline.additivity}{m.grainline.findings ? " ⚠" : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </article>
  );
}
