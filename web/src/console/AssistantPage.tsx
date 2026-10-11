import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { formatPrice } from "../api";
import { useApi } from "./api";

interface Status {
  available: boolean;
  model: string;
  reason: string | null;
}

interface Proposal {
  label: string;
  question: string;
  explanation: string;
  tradeoffs: string[];
  missing_metrics: { name: string; why: string }[];
}

interface Result {
  status: "ready" | "infeasible" | "invalid" | "refused" | "error";
  name?: string;
  decision?: Record<string, unknown>;
  yaml?: string;
  proposal?: Proposal;
  attempts: { attempt: number; problems: string[] }[];
  message?: string;
  preview?: {
    status: string;
    score_label?: string;
    recommended?: { entity: string; name?: string; score: number }[];
    requirements: { missing_by_owner: Record<string, string[]> };
  };
}

const EXAMPLES = [
  "哪些商品賣得好、但最近常缺貨，應該優先補貨？",
  "哪些商品被看很多但很少人買？可能是價格或商品頁有問題。",
  "哪些商品加入購物車的人很多，但最後沒結帳？",
  "哪些商品的退貨率太高，應該下架？",
];

export function AssistantPage() {
  const status = useApi<Status>("/assistant/status");
  const navigate = useNavigate();
  const [request, setRequest] = useState(EXAMPLES[0]);
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setBusy(true);
    setError(null);
    setResult(null);
    const response = await fetch("/api/assistant/playbooks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ request }),
    });
    const body = await response.json();
    setBusy(false);
    if (!response.ok) setError(typeof body.detail === "string" ? body.detail : `HTTP ${response.status}`);
    else setResult(body);
  }

  async function save() {
    if (!result?.name) return;
    const response = await fetch("/api/assistant/playbooks/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: result.name, decision: result.decision }),
    });
    const body = await response.json();
    if (!response.ok) setError(typeof body.detail === "string" ? body.detail : `HTTP ${response.status}`);
    else navigate(`/console/decisions/${body.name}`);
  }

  if (!status.data) return <p className="muted">檢查 AI 設定…</p>;
  return (
    <article className="decision">
      <h1>AI 提案：用一句話寫決策劇本</h1>
      <p className="muted">
        用一句話說你想做的決策。Claude 會在指標庫的範圍內寫出 L5 劇本，規則引擎驗證（可加性、資料夠不夠、實際試跑），不通過就讓它修正。按下儲存才會寫入。
      </p>
      {!status.data.available && (
        <div className="callout">
          <p><b>AI 提案還沒設定。</b>{status.data.reason}</p>
          <p className="small">其他功能（決策、報表、檢查）不受影響。</p>
        </div>
      )}
      <div className="chips">
        {EXAMPLES.map((e) => (
          <button key={e} type="button" onClick={() => setRequest(e)} aria-pressed={request === e}>{e}</button>
        ))}
      </div>
      <label className="field wide">
        你的問題
        <textarea rows={3} value={request} onChange={(e) => setRequest(e.target.value)} />
      </label>
      <button type="button" className="primary" onClick={submit} disabled={busy || !status.data.available || request.trim().length < 4}>
        {busy ? "Claude 撰寫劇本中，規則引擎驗證中…（約 20–60 秒）" : `請 Claude 提案（${status.data.model}）`}
      </button>
      {error && <p className="warning-text">{error}</p>}

      {result && (
        <section>
          <h2>過程</h2>
          <ol className="small">
            {result.attempts.map((a) => (
              <li key={a.attempt}>
                第 {a.attempt} 次提案：{a.problems.length ? <>規則引擎擋下 — {a.problems.join("；")}</> : "通過驗證"}
              </li>
            ))}
          </ol>
          {result.message && <p className="warning-text">{result.message}</p>}

          {result.proposal && (
            <>
              <h2>{result.proposal.label}</h2>
              <p className="lead">{result.proposal.question}</p>
              <p>{result.proposal.explanation}</p>
              {result.proposal.missing_metrics.length > 0 && (
                <div className="callout">
                  <p><b>指標庫還不夠完整回答這個問題，缺：</b></p>
                  <ul>{result.proposal.missing_metrics.map((m) => <li key={m.name}><code>{m.name}</code>：{m.why}</li>)}</ul>
                  <p className="small">需要先把這些指標（以及它需要的資料）加進指標庫；AI 不會自己發明指標。</p>
                </div>
              )}
              {result.proposal.tradeoffs.length > 0 && (
                <>
                  <h3>取捨與限制</h3>
                  <ul className="small">{result.proposal.tradeoffs.map((t) => <li key={t}>{t}</li>)}</ul>
                </>
              )}
            </>
          )}

          {result.status === "ready" && result.preview && (
            <>
              <h3>試跑結果（目前的 tracking plan）</h3>
              {result.preview.status === "ready" ? (
                <ol className="small">
                  {result.preview.recommended!.map((c) => (
                    <li key={c.entity}>{c.name ?? c.entity}（{result.preview!.score_label}：{c.score >= 100 ? formatPrice(Math.round(c.score)) : c.score.toFixed(3)}）</li>
                  ))}
                </ol>
              ) : (
                <p className="small">目前的 tracking plan 還跑不了：{Object.entries(result.preview.requirements.missing_by_owner).map(([o, items]) => `${o}：${items.join("；")}`).join("　")}</p>
              )}
              <details>
                <summary>劇本 YAML</summary>
                <pre className="sql">{result.yaml}</pre>
              </details>
              <div className="actions">
                <button type="button" className="primary" onClick={save}>儲存到 l5_decisions.yaml 並打開</button>
                <Link to="/console/library">看指標庫</Link>
              </div>
            </>
          )}
        </section>
      )}
    </article>
  );
}
