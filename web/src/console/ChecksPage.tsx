import { planQuery, usePlanParam, useApi } from "./api";
import type { ChecksData, Finding } from "./types";

export const RULES = {
  R1: "可加性 / grain",
  R2: "比率母體",
  R3: "血緣可達",
} as const;

function EvidenceTable({ rows }: { rows: Record<string, unknown>[] }) {
  const columns = Object.keys(rows[0]);
  return (
    <div className="table-scroll">
      <table className="data compact">
        <thead><tr>{columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {columns.map((c) => {
                const v = row[c];
                return <td key={c} className={typeof v === "number" ? "num" : undefined}>
                  {typeof v === "number" ? v.toLocaleString("zh-TW", { maximumFractionDigits: 3 }) : String(v ?? "—")}
                </td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function FindingCard({ finding }: { finding: Finding }) {
  const { evidence } = finding;
  return (
    <article className={`finding ${finding.severity}`}>
      <header>
        <span className={`severity ${finding.severity}`}>
          {finding.severity === "error" ? "✕ 錯誤" : "! 注意"}
        </span>
        <span className="rule">{finding.rule} {RULES[finding.rule]}</span>
        <code className="subject">{finding.subject}</code>
      </header>
      <p>{finding.message}</p>
      {evidence?.summary && (
        <p className="evidence"><b>證據</b>　{evidence.summary}</p>
      )}
      {finding.impacted.length > 0 && (
        <p className="small">
          <span className="muted">影響：</span>
          {finding.impacted.map((i) => <code key={i} className="chip-code">{i}</code>)}
        </p>
      )}
      {evidence?.path && (
        <ol className="hops inline">
          {evidence.path.map((h, i) => (
            <li key={i} className={h.detail.includes("✗") ? "broken" : undefined}>
              <span className="layer">{h.layer}</span> <code>{h.name}</code> <span className="muted">{h.detail}</span>
            </li>
          ))}
        </ol>
      )}
      {evidence?.rows && evidence.rows.length > 0 && (
        <details>
          <summary>明細（{evidence.rows.length} 列）</summary>
          <EvidenceTable rows={evidence.rows} />
        </details>
      )}
    </article>
  );
}

export function ChecksPage() {
  const [plan] = usePlanParam();
  const { data, error } = useApi<ChecksData>(`/checks${planQuery(plan)}`, 5000);
  if (error) return <p className="warning-text">{error.message}</p>;
  if (!data) return <p className="muted">在假資料上跑檢查…</p>;
  return (
    <>
      <h1>相容性檢查</h1>
      <p className="muted">
        tracking plan v{data.tracking_plan_version}，{data.generated_events.toLocaleString()} 筆模擬事件，加上 demo 商店的點擊。
        共 {data.summary.error} 個錯誤、{data.summary.warning} 個注意。
      </p>
      {(Object.keys(RULES) as (keyof typeof RULES)[]).map((rule) => {
        const findings = data.findings.filter((f) => f.rule === rule);
        return (
          <section key={rule} className="rule-section">
            <h2>{rule} {RULES[rule]}（{findings.length}）</h2>
            {findings.length === 0 ? <p className="muted small">沒有問題。</p> : findings.map((f) => (
              <FindingCard key={`${f.kind}-${f.subject}`} finding={f} />
            ))}
          </section>
        );
      })}
    </>
  );
}
