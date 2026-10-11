import { formatPrice } from "../api";
import { planQuery, useApi } from "./api";

interface Range {
  mean: number;
  min: number;
  max: number;
}

interface Effect {
  per_run: Record<"units" | "revenue" | "margin", number[]>;
  units: Range;
  revenue: Range;
  margin: Range;
  positive_runs: number;
}

interface ProductEffect {
  product_id: string;
  name: string;
  role: string;
  exposure_before: number;
  exposure_after: number;
  units_before: number;
  units_after: number;
  margin_change: number;
}

interface Execution {
  status: "ready" | "blocked";
  horizon_days: number;
  replicates: number;
  layout: Record<"keep" | "displaced" | "promoted" | "random", { product_id: string; name: string }[]>;
  control: { margin: number; revenue: number; units: number };
  effects: { playbook: Effect; random: Effect };
  skill: { margin: Range; per_run: number[]; better_runs: number };
  products: ProductEffect[];
  explanation: Record<"assumptions" | "method" | "uncertainty" | "limitations", string[]>;
}

const signed = (v: number) => `${v >= 0 ? "+" : "−"}${formatPrice(Math.abs(Math.round(v)))}`;
const n = (v: number) => Math.round(v).toLocaleString("zh-TW");

/** 每組模擬的結果：小點是每一次、粗線是範圍、大點是平均。兩組共用同一條軸，0 在左邊。 */
function RangePlot({ rows }: { rows: { label: string; slot: 1 | 2; runs: number[]; range: Range }[] }) {
  const width = 640;
  const rowH = 44;
  const pad = { left: 110, right: 24, top: 8, bottom: 26 };
  const max = Math.max(...rows.flatMap((r) => r.runs), 0) * 1.08 || 1;
  const min = Math.min(...rows.flatMap((r) => r.runs), 0);
  const x = (v: number) => pad.left + ((v - min) / (max - min)) * (width - pad.left - pad.right);
  const height = pad.top + rows.length * rowH + pad.bottom;
  const ticks = [min, (min + max) / 2, max].map((t) => Math.round(t / 10000) * 10000);
  return (
    <figure className="chart">
      <figcaption><span className="chart-title">{rows[0].runs.length} 次模擬的毛利變化（和維持現狀相比）</span></figcaption>
      <svg viewBox={`0 0 ${width} ${height}`} width="100%" role="img" aria-label="每組模擬的毛利變化範圍">
        {ticks.map((t) => (
          <g key={t}>
            <line className="grid" x1={x(t)} x2={x(t)} y1={pad.top} y2={height - pad.bottom} />
            <text className="axis" x={x(t)} y={height - 8} textAnchor="middle">{signed(t)}</text>
          </g>
        ))}
        {rows.map((row, i) => {
          const y = pad.top + i * rowH + rowH / 2;
          return (
            <g key={row.label}>
              <text className="direct-label" x={pad.left - 10} y={y} dy="0.32em" textAnchor="end">{row.label}</text>
              <line className={`line series-${row.slot}`} x1={x(row.range.min)} x2={x(row.range.max)} y1={y} y2={y} strokeWidth={4} />
              {row.runs.map((v, j) => <circle key={j} className={`marker series-${row.slot}`} cx={x(v)} cy={y} r={4} opacity={0.55} />)}
              <circle className={`marker series-${row.slot}`} cx={x(row.range.mean)} cy={y} r={7}>
                <title>{`${row.label}：平均 ${signed(row.range.mean)}，範圍 ${signed(row.range.min)} 到 ${signed(row.range.max)}`}</title>
              </circle>
            </g>
          );
        })}
      </svg>
    </figure>
  );
}

const SECTIONS: [keyof Execution["explanation"], string][] = [
  ["assumptions", "假設"],
  ["method", "怎麼算"],
  ["uncertainty", "不確定性"],
  ["limitations", "限制"],
];

export function ExecutionSection({ decision, plan }: { decision: string; plan: number | null }) {
  const { data, error } = useApi<Execution>(`/decisions/${decision}/execution${planQuery(plan)}`);
  if (error) return <p className="warning-text">{error.message}</p>;
  if (!data) return <p className="muted">模擬執行中：同一群使用者，跑「維持現狀」「照建議換」「隨機換」各 5 次…</p>;
  if (data.status !== "ready") return null;
  const pb = data.effects.playbook;
  const names = (list: { name: string }[]) => list.map((p) => p.name).join("、");

  return (
    <section>
      <h2>模擬執行：如果照建議換首頁精選，接下來 {data.horizon_days} 天會怎樣？</h2>
      <p className="small">
        首頁精選前 {data.layout.keep.length} 格不動（{names(data.layout.keep)}）；
        換下 <b>{names(data.layout.displaced)}</b>，換上 <b>{names(data.layout.promoted)}</b>。
      </p>
      <div className="tiles">
        <div className="tile primary-tile">
          <div className="tile-value">{signed(pb.margin.mean)}</div>
          <div className="tile-label">毛利變化（平均）</div>
          <div className="muted small">範圍 {signed(pb.margin.min)} 到 {signed(pb.margin.max)}；{pb.positive_runs}/{data.replicates} 次增加；維持現狀約 {formatPrice(Math.round(data.control.margin))}</div>
        </div>
        <div className="tile">
          <div className="tile-value">{signed(data.skill.margin.mean)}</div>
          <div className="tile-label">其中「選得好」的部分</div>
          <div className="muted small">照建議換 − 隨機換（同一組情境逐次相減）；{data.skill.better_runs}/{data.replicates} 次劇本比較好</div>
        </div>
        <div className="tile">
          <div className="tile-value">{pb.units.mean >= 0 ? "+" : "−"}{n(Math.abs(pb.units.mean))} 件</div>
          <div className="tile-label">銷售件數變化（平均）</div>
          <div className="muted small">營收 {signed(pb.revenue.mean)}</div>
        </div>
      </div>
      <RangePlot
        rows={[
          { label: "照建議換", slot: 1, runs: pb.per_run.margin, range: pb.margin },
          { label: "隨機換", slot: 2, runs: data.effects.random.per_run.margin, range: data.effects.random.margin },
        ]}
      />
      <div className="table-scroll">
        <table className="data compact">
          <thead>
            <tr><th></th><th>商品</th><th className="num">曝光</th><th className="num">銷售件數</th><th className="num">毛利變化</th></tr>
          </thead>
          <tbody>
            {data.products.map((p) => (
              <tr key={p.product_id}>
                <td><span className="tag neutral">{p.role}</span></td>
                <td>{p.name} <span className="muted small">{p.product_id}</span></td>
                <td className="num">{n(p.exposure_before)} → {n(p.exposure_after)}</td>
                <td className="num">{p.units_before.toFixed(1)} → {p.units_after.toFixed(1)}</td>
                <td className="num">{signed(p.margin_change)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="explain-grid">
        {SECTIONS.map(([key, title]) => (
          <div key={key} className="explain-block">
            <h3>{title}</h3>
            <ul>{data.explanation[key].map((line, i) => <li key={i}>{line}</li>)}</ul>
          </div>
        ))}
      </div>
    </section>
  );
}
