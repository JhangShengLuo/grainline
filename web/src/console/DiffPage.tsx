import { useState } from "react";
import { useSearchParams } from "react-router-dom";

import type { TrackingPlanSummary } from "../tracking/types";
import { formatDelta, formatValue, useApi, withGaps } from "./api";
import { FindingCard } from "./ChecksPage";
import { LineChart } from "./LineChart";
import type { DiffData, ReportData } from "./types";

const CHANGE_LABEL: Record<string, string> = {
  event_added: "新增事件",
  event_removed: "移除事件",
  fires_on_changed: "送出時機",
  property_added: "新增欄位",
  property_removed: "移除欄位",
  property_changed: "欄位定義",
};

function DailyComparison({ base, target }: { base: number; target: number }) {
  const a = useApi<ReportData>(`/reports/daily_overview?plan=${base}`, 5000);
  const b = useApi<ReportData>(`/reports/daily_overview?plan=${target}`, 5000);
  const [metric, setMetric] = useState("cart_adds");
  if (!a.data || !b.data) return a.error || b.error ? null : <p className="muted">載入每日數字…</p>;
  const info = b.data.metrics.find((m) => m.name === metric) ?? b.data.metrics[0];
  // 只比模擬資料的日期：demo 商店的點擊在兩個版本是不同的人、不同的操作
  const live = new Set([...a.data.live_periods, ...b.data.live_periods]);
  const periods = withGaps(
    [...new Set([...a.data.rows, ...b.data.rows].map((r) => String(r.period)))].filter((p) => !live.has(p)),
    "day",
  );
  const values = (report: ReportData) =>
    periods.map((p) => (p === null ? null : ((report.rows.find((r) => String(r.period) === p)?.[info.name] as number | null) ?? null)));
  return (
    <section>
      <div className="report-head">
        <h2>每日比較</h2>
        <label className="field inline">
          指標
          <select value={info.name} onChange={(e) => setMetric(e.target.value)}>
            {b.data.metrics.map((m) => <option key={m.name} value={m.name}>{m.label}</option>)}
          </select>
        </label>
      </div>
      <LineChart
        title={`${info.label}：同一群使用者、同樣的行為，兩種埋點設計`}
        labels={periods.map((p) => p ?? "…")}
        series={[
          { name: `v${base}`, slot: 1, values: values(a.data) },
          { name: `v${target}`, slot: 2, values: values(b.data) },
        ]}
        format={(v) => formatValue(v, info.format)}
      />
    </section>
  );
}

export function DiffPage() {
  const plans = useApi<TrackingPlanSummary[]>("/tracking-plans");
  const [params, setParams] = useSearchParams();
  const list = plans.data ?? [];
  const defaultBase = list.find((p) => p.default)?.version;
  const base = Number(params.get("base")) || defaultBase;
  const target = Number(params.get("target")) || Math.max(...list.filter((p) => p.version !== base).map((p) => p.version));
  const ready = base !== undefined && Number.isFinite(target);
  const diff = useApi<DiffData>(ready ? `/diff?base=${base}&target=${target}` : null, 5000);

  const set = (key: "base" | "target", value: number) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set(key, String(value));
      return next;
    });

  if (plans.data && list.length < 2) return <p className="muted">只有一個 tracking plan 版本，沒有東西可以比較。</p>;
  const data = diff.data;
  const metrics = data ? [...data.metrics].sort((x, y) => Number(y.changed) - Number(x.changed)) : [];
  const changedReports = data?.reports.filter((r) => Boolean(r.base_broken) !== Boolean(r.target_broken)) ?? [];

  return (
    <>
      <div className="report-head">
        <h1>版本差異</h1>
        {ready && (
          <div className="diff-select">
            {(["base", "target"] as const).map((key) => (
              <label key={key} className="field inline">
                {key === "base" ? "從" : "到"}
                <select value={key === "base" ? base : target} onChange={(e) => set(key, Number(e.target.value))}>
                  {list.map((p) => <option key={p.version} value={p.version}>v{p.version} {p.name}</option>)}
                </select>
              </label>
            ))}
          </div>
        )}
      </div>
      <p className="muted">
        兩個版本用的是同一群模擬使用者、同樣的行為（同一個 seed），只有埋點設計不同，所以下面的差異全部來自 tracking plan。
      </p>
      {diff.error && <p className="warning-text">{diff.error.message}</p>}
      {!data && !diff.error && <p className="muted">建立兩個版本的倉儲並比較…</p>}
      {data && (
        <>
          <section>
            <h2>L1 改了什麼（{data.plan_changes.length}）</h2>
            <ul className="changes">
              {data.plan_changes.map((c, i) => (
                <li key={i}><span className="tag neutral">{CHANGE_LABEL[c.kind] ?? c.kind}</span> {c.message}</li>
              ))}
            </ul>
          </section>

          <section>
            <h2>指標（整段期間）</h2>
            <div className="table-scroll">
              <table className="data">
                <thead>
                  <tr>
                    <th>指標</th>
                    <th className="num">v{data.base.version}</th>
                    <th className="num">v{data.target.version}</th>
                    <th className="num">差異</th>
                    <th>相關的 L1 改動</th>
                  </tr>
                </thead>
                <tbody>
                  {metrics.map((m) => (
                    <tr key={m.name} className={m.changed ? undefined : "unchanged"}>
                      <td>{m.label} <code className="muted small">{m.name}</code></td>
                      <td className="num">{m.base.broken ? <span className="tag">斷鏈</span> : formatValue(m.base.value, m.format)}</td>
                      <td className="num">{m.target.broken ? <span className="tag" title={m.target.broken}>斷鏈</span> : formatValue(m.target.value, m.format)}</td>
                      <td className="num">
                        {formatDelta(m.delta, m.format)}
                        {m.pct !== null && m.delta !== 0 && m.format !== "percent" && (
                          <span className="muted small">（{m.pct > 0 ? "+" : ""}{(m.pct * 100).toFixed(1)}%）</span>
                        )}
                      </td>
                      <td className="small">
                        {m.changed ? m.related_changes.map((c) => <div key={c}>{c}</div>) : <span className="muted">沒有影響</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          {base !== undefined && <DailyComparison base={base} target={target} />}

          {changedReports.length > 0 && (
            <section>
              <h2>報表</h2>
              <ul className="changes">
                {changedReports.map((r) => (
                  <li key={r.name}>
                    <b>{r.label}</b>：{r.target_broken ? `v${data.target.version} 無法建立（${r.target_broken}）` : `v${data.target.version} 可以建立了（v${data.base.version}：${r.base_broken}）`}
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section>
            <h2>新出現的問題（{data.findings.added.length}）</h2>
            {data.findings.added.length === 0 ? <p className="muted small">沒有。</p> : data.findings.added.map((f) => (
              <FindingCard key={`${f.kind}-${f.subject}`} finding={f} />
            ))}
            <h2>解決的問題（{data.findings.resolved.length}）</h2>
            {data.findings.resolved.length === 0 ? <p className="muted small">沒有。</p> : data.findings.resolved.map((f) => (
              <p key={`${f.kind}-${f.subject}`} className="small">✓ <code>{f.subject}</code> {f.message}</p>
            ))}
          </section>
        </>
      )}
    </>
  );
}
