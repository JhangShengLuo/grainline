import { useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";

import { formatPrice } from "../api";
import { planQuery, usePlanParam, useApi } from "./api";
import { ExecutionSection } from "./ExecutionSection";
import { LineChart } from "./LineChart";

interface Check {
  input: string;
  op: string;
  threshold: number;
  threshold_label: string | null;
  value: number;
  passed: boolean;
  why: string;
}

interface Candidate {
  entity: string;
  name?: string;
  category?: string;
  values: Record<string, number>;
  raw_rate: number | null;
  rate: number;
  multiplier: number;
  score: number;
  passed: boolean;
  checks: Check[];
}

interface Requirements {
  ready: boolean;
  missing_by_owner: Record<string, string[]>;
  inputs: { alias: string; metric: string; label: string; status: string; problems?: string[] }[];
  layers: {
    metrics: { name: string; label: string; status: string }[];
    models: string[];
    events: { event: string; moments: string[] }[];
    sources: { source: string; owner: string }[];
    moments: { moment: string; text: string; where: string }[];
  };
}

interface DecisionResult {
  decision: string;
  label: string;
  question: string;
  status: "ready" | "blocked";
  top_k: number;
  tracking_plan_version: number;
  requirements: Requirements;
  window?: { start: string; end: string };
  score_label?: string;
  overall?: { rate: number; multiplier: number; score: number; totals: Record<string, number> };
  rules?: { input: string; op_text: string; value: number | string; threshold: number; why: string }[];
  inputs?: Record<string, string>;
  recommended?: Candidate[];
  candidates?: Candidate[];
}

interface Pick {
  label?: string;
  description?: string;
  picked: string[];
  hits: string[];
  precision: number;
}

interface Evaluation {
  status: "ready" | "blocked";
  k: number;
  potential: string[];
  note: string;
  playbook?: Pick;
  baselines?: Record<string, Pick>;
  curve?: { day: number; date: string; precision: number; hits: number }[];
  first_detected?: Record<string, number>;
}

interface Proposal {
  base_version: number;
  version: number;
  needed: boolean;
  changes: { kind: string; message: string }[];
  filename: string;
  yaml: string;
}

const INPUT_LABEL: Record<string, string> = {
  impressions: "曝光",
  views: "商品頁瀏覽",
  units: "銷售件數",
  margin: "毛利",
  stockout_days: "缺貨天數",
};
const pct = (v: number | null) => (v === null ? "—" : `${(v * 100).toFixed(1)}%`);
const num = (v: number) => v.toLocaleString("zh-TW", { maximumFractionDigits: 0 });

function Flow({ req }: { req: Requirements }) {
  const { layers } = req;
  return (
    <div className="flow">
      <div className="flow-col">
        <h4>L5 決策</h4>
        <div className="flow-node">{req.inputs.length} 個輸入</div>
      </div>
      <div className="flow-col">
        <h4>L4 指標</h4>
        {layers.metrics.map((m) => (
          <Link key={m.name} to={`/console/metrics/${m.name}`} className={`flow-node ${m.status}`}>{m.label}</Link>
        ))}
      </div>
      <div className="flow-col">
        <h4>L3 表</h4>
        {layers.models.length ? layers.models.map((m) => <div key={m} className="flow-node"><code>{m}</code></div>)
          : <div className="flow-node muted">（要先補資料）</div>}
      </div>
      <div className="flow-col">
        <h4>L1 事件／業務資料</h4>
        {layers.events.map((e) => <div key={e.event} className="flow-node"><code>{e.event}</code><span className="muted small">前端</span></div>)}
        {layers.sources.map((s) => <div key={s.source} className="flow-node"><code>{s.source}</code><span className="muted small">{s.owner}</span></div>)}
      </div>
      <div className="flow-col">
        <h4>前端要在哪裡埋</h4>
        {layers.moments.map((m) => (
          <div key={m.moment} className="flow-node"><b>{m.where}</b><span className="muted small">{m.text}</span></div>
        ))}
      </div>
    </div>
  );
}

function Blocked({ result, plan }: { result: DecisionResult; plan: number | null }) {
  const navigate = useNavigate();
  const preview = useApi<Proposal>(`/decisions/${result.decision}/proposal${planQuery(plan)}`);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function create() {
    setCreating(true);
    setError(null);
    const response = await fetch(`/api/decisions/${result.decision}/proposal${planQuery(plan)}`, { method: "POST" });
    const body = await response.json();
    setCreating(false);
    if (!response.ok) {
      setError(typeof body.detail === "string" ? body.detail : `HTTP ${response.status}`);
      return;
    }
    navigate(`/console/decisions/${result.decision}?plan=${body.version}`);
  }

  return (
    <div className="callout">
      <p><b>tracking plan v{result.tracking_plan_version} 沒辦法執行這個劇本。</b>缺的東西：</p>
      <ul>
        {Object.entries(result.requirements.missing_by_owner).map(([owner, items]) => (
          <li key={owner}><b>{owner}</b>：{items.join("；")}</li>
        ))}
      </ul>
      {preview.data?.needed && (
        <>
          <p className="small">
            可以直接產生一份補齊的 tracking plan 提案（v{preview.data.version}，以 v{preview.data.base_version} 為基礎）：
          </p>
          <ul className="small">{preview.data.changes.map((c, i) => <li key={i}>{c.message}</li>)}</ul>
          <button type="button" className="primary" onClick={create} disabled={creating}>
            {creating ? "產生中…" : `產生 tracking plan 提案 v${preview.data.version}`}
          </button>
          <span className="muted small">　會寫入 <code>specs/shop/{preview.data.filename}</code>，前端與 PM 確認後再採用。</span>
          {error && <p className="warning-text small">{error}</p>}
        </>
      )}
    </div>
  );
}

function Recommended({ result }: { result: DecisionResult }) {
  const overall = result.overall!;
  return (
    <ol className="picks">
      {result.recommended!.map((c, i) => (
        <li key={c.entity} className="card pick">
          <div className="pick-head">
            <span className="rank">{i + 1}</span>
            <div>
              <b>{c.name ?? c.entity}</b> <span className="muted small">{c.category} · {c.entity}</span>
            </div>
            <div className="pick-score" title={result.score_label}>
              {formatPrice(Math.round(c.score))}<span className="muted small"> / 每次瀏覽</span>
            </div>
          </div>
          <dl className="pick-stats">
            <div><dt>曝光</dt><dd>{num(c.values.impressions)}</dd></div>
            <div><dt>商品頁瀏覽</dt><dd>{num(c.values.views)}</dd></div>
            <div><dt>銷售件數</dt><dd>{num(c.values.units)}</dd></div>
            <div><dt>轉換率</dt><dd>{pct(c.raw_rate)} <span className="muted small">→ 收斂後 {pct(c.rate)}（全站 {pct(overall.rate)}）</span></dd></div>
            <div><dt>每件毛利</dt><dd>{formatPrice(Math.round(c.multiplier))}</dd></div>
          </dl>
          <ul className="checks">
            {c.checks.map((ch) => <li key={ch.input}>✓ {ch.why}</li>)}
          </ul>
        </li>
      ))}
    </ol>
  );
}

function EvaluationSection({ decision, plan }: { decision: string; plan: number | null }) {
  const { data } = useApi<Evaluation>(`/decisions/${decision}/evaluation${planQuery(plan)}`);
  if (!data) return <p className="muted">用模擬器的真相驗證中…</p>;
  if (data.status !== "ready") return null;
  const tiles: [string, string, Pick][] = [
    ["這個劇本", "收斂後轉換率 × 每件毛利，只看被埋沒的商品", data.playbook!],
    ...Object.values(data.baselines!).map((b): [string, string, Pick] => [b.label!, b.description!, b]),
  ];
  const firstDay = data.curve!.find((c) => c.hits >= 2)?.day;
  return (
    <section>
      <h2>準不準？用模擬器的真相驗證</h2>
      <p className="muted small">{data.note} 這一次共有 {data.potential.length} 個。</p>
      <div className="tiles">
        {tiles.map(([label, description, pick], i) => (
          <div key={label} className={`tile ${i === 0 ? "primary-tile" : ""}`}>
            <div className="tile-value">{pick.hits.length} / {data.k}</div>
            <div className="tile-label">{label}選中的真正潛力商品</div>
            <div className="muted small">{description}</div>
          </div>
        ))}
      </div>
      <LineChart
        title={`只用前 N 天的資料時，劇本前 ${data.k} 名裡有幾個是真正的潛力商品`}
        labels={data.curve!.map((c) => `第 ${c.day} 天`)}
        series={[{ name: "劇本", slot: 1, values: data.curve!.map((c) => c.hits) }]}
        format={(v) => `${Math.round(v)}`}
        height={200}
      />
      <p className="muted small">
        {firstDay ? `第 ${firstDay} 天起，前 ${data.k} 名裡至少有 2 個是真正的潛力商品。` : ""}
        越早越好，但資料越少越容易看錯：劇本要求至少 20 次商品頁瀏覽才列入，這是準確和速度之間的取捨，可以在 l5_decisions.yaml 調整。
      </p>
    </section>
  );
}

export function DecisionPage() {
  const { name = "" } = useParams();
  const [plan] = usePlanParam();
  const [asOf, setAsOf] = useState<number | null>(null);
  const query = `${planQuery(plan)}${asOf ? `${plan ? "&" : "?"}as_of_day=${asOf}` : ""}`;
  const { data, error } = useApi<DecisionResult>(`/decisions/${name}${query}`);
  if (error) return <p className="warning-text">{error.message}</p>;
  if (!data) return <p className="muted">執行劇本…</p>;

  return (
    <article className="decision">
      <p className="small muted">L5 決策劇本 · tracking plan v{data.tracking_plan_version}</p>
      <h1>{data.label}</h1>
      <p className="lead">{data.question}</p>

      {data.status === "blocked" ? (
        <Blocked result={data} plan={plan} />
      ) : (
        <>
          <section>
            <div className="report-head">
              <h2>建議多給曝光的 {data.recommended!.length} 個商品</h2>
              <label className="field inline">
                只用前
                <input
                  type="range" min={7} max={28} value={asOf ?? 28}
                  onChange={(e) => setAsOf(Number(e.target.value) === 28 ? null : Number(e.target.value))}
                />
                {asOf ?? 28} 天的資料
              </label>
            </div>
            <p className="muted small">
              資料期間 {data.window!.start} 到 {data.window!.end}。依「{data.score_label}」排序；只是建議，不會自動調整版位或預算。
            </p>
            {data.recommended!.length ? <Recommended result={data} /> : <p className="muted">目前沒有符合所有條件的商品。</p>}
            <details>
              <summary>入選條件與全部 {data.candidates!.length} 個商品</summary>
              <ul className="small">
                {data.rules!.map((r) => (
                  <li key={r.input}>{INPUT_LABEL[r.input] ?? r.input} {r.op_text} {typeof r.value === "string" ? `${r.value}（${num(r.threshold)}）` : num(r.threshold)}：{r.why}</li>
                ))}
              </ul>
              <div className="table-scroll">
                <table className="data compact">
                  <thead>
                    <tr><th>商品</th>{Object.keys(data.inputs!).map((k) => <th key={k} className="num">{INPUT_LABEL[k] ?? k}</th>)}<th className="num">分數</th><th>條件</th></tr>
                  </thead>
                  <tbody>
                    {data.candidates!.map((c) => (
                      <tr key={c.entity} className={c.passed ? "marked" : undefined}>
                        <td>{c.name ?? c.entity}</td>
                        {Object.keys(data.inputs!).map((k) => <td key={k} className="num">{num(c.values[k])}</td>)}
                        <td className="num">{num(c.score)}</td>
                        <td>{c.checks.map((ch) => <span key={ch.input} title={ch.why}>{ch.passed ? "✓" : "✕"}</span>)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          </section>
          <EvaluationSection decision={name} plan={plan} />
          <ExecutionSection decision={name} plan={plan} />
        </>
      )}

      <section>
        <h2>以終為始：這個決策需要什麼</h2>
        <p className="muted small">從決策往回推：需要哪些指標、哪些表、哪些事件與業務資料，前端要在哪個頁面、哪個動作埋。點指標可以看它怎麼算。</p>
        <Flow req={data.requirements} />
      </section>
    </article>
  );
}

export function DecisionsHome() {
  const [plan] = usePlanParam();
  const { data } = useApi<{ name: string }[]>(`/decisions${planQuery(plan)}`);
  if (!data) return <p className="muted">載入決策劇本…</p>;
  if (!data.length) return <p className="muted">還沒有決策劇本：在 specs/shop/l5_decisions.yaml 加一個。</p>;
  return <Navigate to={{ pathname: `/console/decisions/${data[0].name}`, search: planQuery(plan) }} replace />;
}
