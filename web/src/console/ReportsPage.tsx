import { useState } from "react";
import { useSearchParams } from "react-router-dom";

import { ApiError, formatValue, planQuery, usePlanParam, useApi, withGaps } from "./api";
import { LineagePanel } from "./LineagePanel";
import { LineChart, type Series } from "./LineChart";
import type { ReportData, ReportSummary } from "./types";

const GRAIN_LABEL: Record<string, string> = { day: "每日", week: "每週（週一起算）", month: "每月" };

function ReportChart({ report, metric }: { report: ReportData; metric: string }) {
  const info = report.metrics.find((m) => m.name === metric)!;
  const periods = withGaps([...new Set(report.rows.map((r) => String(r.period)))], report.time_grain);
  const keyOf = (row: Record<string, unknown>) => report.dimensions.map((d) => String(row[d] ?? "（空）")).join(" / ");
  const groups = report.dimensions.length ? [...new Set(report.rows.map(keyOf))] : [info.label];

  if (groups.length > 2) {
    return (
      <p className="muted small chart-note">
        這張報表依 {report.dimensions.join("、")} 分成 {groups.length} 組，請看下方表格。
        {info.additivity !== "additive" && " 注意：這個指標不可加，不能把各組加起來當總數。"}
      </p>
    );
  }
  const series: Series[] = groups.map((group, i) => ({
    name: group,
    slot: (i + 1) as 1 | 2,
    values: periods.map((p) => {
      if (p === null) return null;
      const row = report.rows.find((r) => String(r.period) === p && (!report.dimensions.length || keyOf(r) === group));
      return row ? (row[metric] as number | null) : null;
    }),
  }));
  return (
    <LineChart
      title={`${info.label}（${GRAIN_LABEL[report.time_grain] ?? report.time_grain}）`}
      labels={periods.map((p) => p ?? "…")}
      series={series}
      format={(v) => formatValue(v, info.format)}
    />
  );
}

export function ReportsPage() {
  const [plan] = usePlanParam();
  const [params, setParams] = useSearchParams();
  const reports = useApi<ReportSummary[]>(`/reports${planQuery(plan)}`);
  const name = params.get("report") ?? reports.data?.[0]?.name ?? null;
  const report = useApi<ReportData>(name ? `/reports/${name}${planQuery(plan)}` : null, 3000);
  const [chartMetric, setChartMetric] = useState<string | null>(null);
  const [lineage, setLineage] = useState<string | null>(null);

  const select = (next: string) =>
    setParams((prev) => {
      const p = new URLSearchParams(prev);
      p.set("report", next);
      return p;
    });

  const data = report.data?.name === name ? report.data : null;
  const metric = data && (data.metrics.some((m) => m.name === chartMetric) ? chartMetric! : data.metrics[0].name);
  const broken = report.error instanceof ApiError && report.error.status === 409
    ? (report.error.detail as { reason: string }).reason
    : null;

  return (
    <div className="with-panel">
      <div className="panel-main">
        <nav className="chips" aria-label="報表">
          {reports.data?.map((r) => (
            <button
              key={r.name}
              type="button"
              aria-current={r.name === name ? "page" : undefined}
              onClick={() => select(r.name)}
              title={r.broken ?? undefined}
            >
              {r.label}
              {r.broken && <span className="tag">斷鏈</span>}
            </button>
          ))}
        </nav>

        {broken && (
          <div className="callout">
            <p><b>這張報表在這個 tracking plan 下無法建立。</b></p>
            <p className="small">{broken}</p>
          </div>
        )}
        {!broken && report.error && <p className="warning-text">{report.error.message}</p>}

        {data && metric && (
          <>
            <div className="report-head">
              <h1>{data.label}</h1>
              <label className="field inline">
                圖表指標
                <select value={metric} onChange={(e) => setChartMetric(e.target.value)}>
                  {data.metrics.map((m) => (
                    <option key={m.name} value={m.name}>{m.label}</option>
                  ))}
                </select>
              </label>
            </div>
            <ReportChart report={data} metric={metric} />
            <p className="muted small">
              點表頭的指標名稱，可以看到它從哪些埋點、經過哪些步驟算出來。
              {data.live_periods.length > 0 && " 標示「含商店點擊」的列包含 demo 商店送出的事件，每 3 秒更新。"}
            </p>
            <div className="table-scroll">
              <table className="data">
                <thead>
                  <tr>
                    <th>期間</th>
                    {data.dimensions.map((d) => <th key={d}>{d}</th>)}
                    {data.metrics.map((m) => (
                      <th key={m.name} className="num">
                        <button type="button" className="link" onClick={() => setLineage(m.name)}>
                          {m.label}
                        </button>
                        {m.additivity !== "additive" && <span className="muted small" title="不可加：不能跨期間或跨維度加總"> ∅Σ</span>}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.rows.map((row, i) => {
                    const live = data.live_periods.includes(String(row.period));
                    return (
                      <tr key={i} className={live ? "live" : undefined}>
                        <td>
                          {String(row.period)}
                          {live && <span className="tag live-tag">含商店點擊</span>}
                        </td>
                        {data.dimensions.map((d) => <td key={d}>{row[d] === null ? "（空）" : String(row[d])}</td>)}
                        {data.metrics.map((m) => (
                          <td key={m.name} className="num">{formatValue(row[m.name] as number | null, m.format)}</td>
                        ))}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </>
        )}
        {!data && !report.error && <p className="muted">載入中…</p>}
      </div>
      {lineage && <LineagePanel metric={lineage} plan={plan} onClose={() => setLineage(null)} />}
    </div>
  );
}
