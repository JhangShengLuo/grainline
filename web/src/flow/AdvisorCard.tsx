import { MODEL_TEXT, money, percent } from "../api";
import type { Missing, Model, Plan, Question } from "../types";

export function AdvisorCard({ question, plans, plan, onPlan, attribution, onAttribution, fanout, onFanout, missing, onDecide, onShowMissing }: {
  question: Question;
  plans: Plan[];
  plan: number;
  onPlan: (v: number) => void;
  attribution: Model | null;
  onAttribution: (m: Model) => void;
  fanout: boolean;
  onFanout: (on: boolean) => void;
  missing: Missing[];
  onDecide: () => void;
  onShowMissing: (m: Missing) => void;
}) {
  const problems = missing.filter((m) => m.status === "missing");
  return (
    <section className="advisor" aria-label="顧問意見">
      <div className="stance">
        <span className="tag">顧問</span>
        {question.stance}
      </div>
      <div className="controls">
        {question.attribution && (
          <div className="segmented" role="radiogroup" aria-label="歸因方式">
            {question.attribution.options.map((m) => (
              <button key={m} role="radio" aria-checked={m === attribution} className={m === attribution ? "on" : ""} onClick={() => onAttribution(m)}>
                {MODEL_TEXT[m]}
                {m === question.attribution!.default && <small> 建議</small>}
              </button>
            ))}
          </div>
        )}
        <label className="toggle">
          <input type="checkbox" checked={fanout} onChange={(e) => onFanout(e.target.checked)} />
          錯誤示範：直接 join
        </label>
        <div className="segmented" role="radiogroup" aria-label="埋點版本">
          {plans.map((p) => (
            <button key={p.version} role="radio" aria-checked={p.version === plan} className={p.version === plan ? "on" : ""} onClick={() => onPlan(p.version)} title={p.name}>
              埋點 v{p.version}
            </button>
          ))}
        </div>
        <button className="primary" onClick={onDecide}>定案</button>
      </div>
      <div className="checks">
        <span className="grain-badge">粒度：{question.grain}</span>
        {missing.map((m) => (
          <button key={`${m.event}.${m.param}`} className={`check ${m.status}`} onClick={() => onShowMissing(m)}
            title={`${m.why}；${m.present}/${m.hits} 個 hit 有這個參數`}>
            {m.status === "missing" ? "缺" : m.status === "partial" ? "部分" : "有"} {m.event}.{m.param}
            {m.status !== "ok" && <span className="rate"> {percent(m.rate)}</span>}
          </button>
        ))}
        {problems.filter((m) => m.impact).map((m) => (
          <span key={m.param} className="impact">
            {money(m.impact!.value)}（{percent(m.impact!.share)}）{m.impact!.label}
          </span>
        ))}
      </div>
    </section>
  );
}
