import { Link } from "react-router-dom";

import { planQuery, useApi } from "./api";
import type { Hop, LineageNode } from "./types";

const ROLE_LABEL: Record<string, string> = { time_column: "時間", measure: "計算", filter: "篩選" };
const LAYER_LABEL: Record<string, string> = { L4: "L4 指標", L3: "L3 模型", L2: "L2 解析", L1: "L1 埋點" };

function HopChain({ hops }: { hops: Hop[] }) {
  return (
    <ol className="hops">
      {hops.map((hop, i) => (
        <li key={i} className={hop.detail.includes("✗") ? "broken" : undefined}>
          <span className="layer">{LAYER_LABEL[hop.layer] ?? hop.layer}</span>
          <code>{hop.name}</code>
          <span className="muted">{hop.detail}</span>
        </li>
      ))}
    </ol>
  );
}

function describe(node: LineageNode): string {
  if (node.type === "ratio") return `${node.numerator!.label} ÷ ${node.denominator!.label}（${node.kind === "rate" ? "比率" : "每單位平均"}）`;
  if (node.type === "rollup") return `先算每${node.from_grain === "day" ? "日" : "週"}的${node.of!.label}，再 ${node.agg}`;
  const m = node.measure!;
  const measure = m.agg === "count" ? "count(*)" : `${m.agg}(${m.column})`;
  const filters = node.filters?.length
    ? `，篩選 ${node.filters.map((f) => `${f.column} ${f.op} ${JSON.stringify(f.value)}`).join("、")}`
    : "";
  return `${node.model} 上的 ${measure}${filters}`;
}

function Node({ node, depth = 0 }: { node: LineageNode; depth?: number }) {
  return (
    <section className={`lineage-node depth-${depth}`}>
      <h3>
        {node.label} <code className="muted">{node.metric}</code>
      </h3>
      <p className="muted small">{describe(node)}</p>
      {node.broken && <p className="warning-text small">⚠ 血緣斷掉：{node.broken}</p>}
      {node.fields?.map((f) => (
        <div key={`${f.role}-${f.column}`} className="lineage-field">
          <p className="small">
            <b>{ROLE_LABEL[f.role] ?? f.role}</b> <code>{f.column}</code>
            {f.type && <span className="muted">（{f.type}）</span>}
          </p>
          <HopChain hops={f.hops} />
        </div>
      ))}
      {node.numerator && <Node node={node.numerator} depth={depth + 1} />}
      {node.denominator && <Node node={node.denominator} depth={depth + 1} />}
      {node.of && <Node node={node.of} depth={depth + 1} />}
    </section>
  );
}

/** 一個指標從 L4 一路追到 L1 埋點：這個數字是由哪些使用者行為、怎麼算出來的 */
export function LineagePanel({ metric, plan, onClose }: { metric: string; plan: number | null; onClose: () => void }) {
  const { data, error } = useApi<LineageNode>(`/metrics/${metric}/lineage${planQuery(plan)}`);
  return (
    <aside className="lineage-panel" aria-label="計算路徑">
      <div className="panel-head">
        <h2>計算路徑</h2>
        <button type="button" className="link" onClick={onClose}>關閉</button>
      </div>
      <p className="small">
        <Link to={{ pathname: `/console/metrics/${metric}`, search: planQuery(plan) }}>看白話說明與實際例子 →</Link>
      </p>
      {error && <p className="warning-text">{error.message}</p>}
      {data ? <Node node={data} /> : !error && <p className="muted">載入中…</p>}
    </aside>
  );
}
