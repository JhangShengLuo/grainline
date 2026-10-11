import { useState } from "react";
import { Link } from "react-router-dom";

import { formatPrice } from "../api";
import { useApi } from "./api";

interface Range {
  mean: number;
  min: number;
  max: number;
}

interface Pick {
  product_id: string;
  name: string;
  potential: boolean;
}

interface Story {
  note: string;
  traditional: {
    weeks: number;
    steps: { week: number; who: string; what: string; doc?: string; impact?: string }[];
    picks: Pick[];
    effect: { margin: Range; revenue: Range; units: Range };
    hits: number;
  };
  grainline: {
    steps: { who: string; what: string; seconds: number | null }[];
    picks: Pick[];
    effect: { margin: Range; revenue: Range; units: Range };
    hits: number;
    compute_seconds: number;
  };
  comparison: { margin_gap: number; control_margin: number; horizon_days: number; replicates: number };
}

interface Markets {
  markets: { seed: number; playbook_hits: number; traditional_hits: number; playbook_margin: number; traditional_margin: number }[];
  k: number;
  wins: number;
  mean_gap: number;
  note: string;
}

const signed = (v: number) => `${v >= 0 ? "+" : "−"}${formatPrice(Math.abs(Math.round(v)))}`;

function Picks({ picks }: { picks: Pick[] }) {
  return (
    <ul className="story-picks">
      {picks.map((p) => (
        <li key={p.product_id} className={p.potential ? "hit" : undefined}>
          {p.potential ? "✓" : "·"} {p.name}
        </li>
      ))}
    </ul>
  );
}

function MarketCheck() {
  const [run, setRun] = useState(false);
  const { data, error } = useApi<Markets>(run ? "/story/markets" : null);
  if (!run) {
    return (
      <button type="button" onClick={() => setRun(true)}>
        在 6 個不同的模擬市場重複驗證（約 30 秒）
      </button>
    );
  }
  if (error) return <p className="warning-text">{error.message}</p>;
  if (!data) return <p className="muted">建立 6 個不同的市場，各跑劇本與傳統做法…</p>;
  return (
    <>
      <p>
        <b>{data.wins} / {data.markets.length} 個市場</b>裡劇本賺得比較多，平均每 14 天多賺 <b>{signed(data.mean_gap)}</b>。
        <span className="muted small">{data.note}</span>
      </p>
      <div className="table-scroll">
        <table className="data compact">
          <thead>
            <tr><th>市場</th><th className="num">劇本選中</th><th className="num">傳統選中</th><th className="num">劇本毛利變化</th><th className="num">傳統毛利變化</th></tr>
          </thead>
          <tbody>
            {data.markets.map((m) => (
              <tr key={m.seed}>
                <td>#{m.seed}</td>
                <td className="num">{m.playbook_hits} / {data.k}</td>
                <td className="num">{m.traditional_hits} / {data.k}</td>
                <td className="num">{signed(m.playbook_margin)}</td>
                <td className="num">{signed(m.traditional_margin)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

export function StoryPage() {
  const { data, error } = useApi<Story>("/story");
  return (
    <article className="story">
      <p className="eyebrow">Grainline</p>
      <h1>在前端寫第一行埋點之前，先讓每個數字「怎麼數、數誰、用哪個時間點」有合約，並先用合成資料看它會不會算錯。</h1>
      <p className="lead muted">Grain contracts for e-commerce analytics: agree on how every metric is counted, before a single tracking event ships.</p>

      <h2>同一個需求：找出被埋沒、但最能賺錢的商品</h2>
      {error && <p className="warning-text">{error.message}</p>}
      {!data && !error && <p className="muted">用模擬器跑兩種做法…（第一次約 10 秒）</p>}
      {data && (
        <>
          <p className="muted small">{data.note}</p>
          <div className="story-columns">
            <section className="story-col">
              <h3>傳統做法：開會、文件往返（示意約 {data.traditional.weeks} 週）</h3>
              <ol className="story-steps">
                {data.traditional.steps.map((s) => (
                  <li key={s.week}>
                    <span className="week">第 {s.week} 週</span>
                    <div>
                      <b>{s.who}</b>：{s.what}
                      {s.doc && <div className="doc-chip">📄 {s.doc}</div>}
                      {s.impact && <div className="impact">! {s.impact}</div>}
                    </div>
                  </li>
                ))}
              </ol>
            </section>
            <section className="story-col">
              <h3>Grainline：從決策往回推（計算 {data.grainline.compute_seconds.toFixed(1)} 秒，加上人的覆核）</h3>
              <ol className="story-steps">
                {data.grainline.steps.map((s, i) => (
                  <li key={i}>
                    <span className="week">{s.seconds === null ? "人" : `${s.seconds.toFixed(1)} 秒`}</span>
                    <div><b>{s.who}</b>：{s.what}</div>
                  </li>
                ))}
              </ol>
            </section>
          </div>

          <h2>結果（模擬器算的，接下來 {data.comparison.horizon_days} 天，{data.comparison.replicates} 次模擬平均）</h2>
          <div className="story-columns">
            <section className="story-col">
              <div className="tile">
                <div className="tile-value">{data.traditional.hits} / {data.traditional.picks.length}</div>
                <div className="tile-label">首頁放銷量前幾名：其中真正的潛力商品</div>
                <div className="muted small">毛利變化 {signed(data.traditional.effect.margin.mean)}（{signed(data.traditional.effect.margin.min)} 到 {signed(data.traditional.effect.margin.max)}）</div>
              </div>
              <Picks picks={data.traditional.picks} />
            </section>
            <section className="story-col">
              <div className="tile primary-tile">
                <div className="tile-value">{data.grainline.hits} / {data.grainline.picks.length}</div>
                <div className="tile-label">劇本建議：其中真正的潛力商品</div>
                <div className="muted small">毛利變化 {signed(data.grainline.effect.margin.mean)}（{signed(data.grainline.effect.margin.min)} 到 {signed(data.grainline.effect.margin.max)}）</div>
              </div>
              <Picks picks={data.grainline.picks} />
            </section>
          </div>
          <p className="small muted">✓ = 模擬器知道的真正潛力商品。只看一個市場不夠，結論要在不同的市場都成立：</p>
          <MarketCheck />

          <h2>自己看看</h2>
          <div className="actions">
            <Link to="/console/decisions/potential_products" className="button primary">打開潛力商品劇本</Link>
            <Link to="/console/decisions/potential_products?plan=2" className="button">前端提案（v2）會發生什麼</Link>
            <Link to="/console/diff" className="button">v1 vs v2 差異</Link>
            <Link to="/shop" className="button">逛 demo 商店（看埋點即時進報表）</Link>
          </div>
        </>
      )}
    </article>
  );
}
