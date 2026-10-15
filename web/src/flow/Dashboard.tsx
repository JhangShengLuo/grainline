import { campaignText, count, MODEL_TEXT, money, percent } from "../api";
import type { AssistRow, BudgetRow, Model, QuestionResult, SkuRow } from "../types";

export function Dashboard({ result, fanout, hover, onHover }: {
  result: QuestionResult;
  fanout: boolean;
  hover: string | null;
  onHover: (key: string | null) => void;
}) {
  const id = result.question.id;
  return (
    <section className="dashboard" aria-label="儀表板">
      {id === "budget" && <Budget result={result} fanout={fanout} hover={hover} onHover={onHover} />}
      {id === "assists" && <Assists result={result} fanout={fanout} hover={hover} onHover={onHover} />}
      {id === "category" && <Category result={result} fanout={fanout} />}
    </section>
  );
}

function Wrong({ value, right, format }: { value: number | undefined; right: number; format: (v: number) => string }) {
  if (value === undefined) return <span className="num">{format(right)}</span>;
  return (
    <span className="num wrong" title={`正確是 ${format(right)}`}>
      {format(value)}
      <small> ×{(value / Math.max(right, 1e-9)).toFixed(1)}</small>
    </span>
  );
}

function Budget({ result, fanout, hover, onHover }: { result: QuestionResult; fanout: boolean; hover: string | null; onHover: (k: string | null) => void }) {
  const rows = result.dashboard.rows as BudgetRow[];
  const model = (result.attribution ?? "last") as Model;
  const per = result.fanout.per_key;
  const max = Math.max(...rows.map((r) => Math.max(fanout ? (per[r.campaign]?.revenue ?? 0) : 0, r.last, r.first, r.linear)), 1);
  return (
    <>
      <div className="dash-head">
        <h2>各活動帶來的銷售額 · {MODEL_TEXT[model]}</h2>
        <span className="muted">總銷售額 {money(result.dashboard.total ?? 0)}</span>
      </div>
      <table className="bars">
        <thead>
          <tr><th>活動</th><th className="bar-col">銷售額</th><th>訂單數</th><th>首次 / 最後 / 線性</th></tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const wrong = fanout ? per[r.campaign] : undefined;
            const pseudo = r.campaign.startsWith("(");
            const value = wrong?.revenue ?? r.revenue;
            return (
              <tr key={r.campaign} className={`${pseudo ? "pseudo" : ""} ${hover === r.campaign ? "hover" : ""} ${r.campaign === "(無法歸因)" ? "lost" : ""}`}
                onMouseEnter={() => onHover(r.campaign)} onMouseLeave={() => onHover(null)}>
                <td>{campaignText(r.campaign)}</td>
                <td className="bar-col">
                  <div className="bar-track">
                    <div className={`bar ${wrong ? "bar-wrong" : ""} ${pseudo ? "bar-pseudo" : ""}`} style={{ width: `${(value / max) * 100}%` }} />
                  </div>
                  <Wrong value={wrong?.revenue} right={r.revenue} format={money} />
                </td>
                <td><Wrong value={wrong?.orders} right={r.orders} format={count} /></td>
                <td className="muted small">{pseudo ? "—" : `${money(r.first)} / ${money(r.last)} / ${money(r.linear)}`}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </>
  );
}

function Assists({ result, fanout, hover, onHover }: { result: QuestionResult; fanout: boolean; hover: string | null; onHover: (k: string | null) => void }) {
  const rows = result.dashboard.rows as AssistRow[];
  const per = result.fanout.per_key;
  const max = Math.max(...rows.map((r) => r.touched_orders), 1);
  return (
    <>
      <div className="dash-head">
        <h2>每個活動碰過的訂單：自己成交 vs 助攻</h2>
        <span className="legend"><i className="sw sw-last" />最後點擊成交 <i className="sw sw-assist" />助攻</span>
      </div>
      <table className="bars">
        <thead>
          <tr><th>活動</th><th className="bar-col">碰過的訂單</th><th>助攻</th><th>線性營收 vs 最後點擊</th></tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const wrong = fanout ? per[r.campaign] : undefined;
            return (
              <tr key={r.campaign} className={hover === r.campaign ? "hover" : ""} onMouseEnter={() => onHover(r.campaign)} onMouseLeave={() => onHover(null)}>
                <td>{campaignText(r.campaign)}</td>
                <td className="bar-col">
                  <div className="bar-track stacked">
                    <div className="bar" style={{ width: `${(r.last_orders / max) * 100}%` }} />
                    <div className="bar bar-assist" style={{ width: `${(r.assist_orders / max) * 100}%` }} />
                  </div>
                  <span className="num">{count(r.touched_orders)}</span>
                </td>
                <td className="num">{count(r.assist_orders)}</td>
                <td>
                  <Wrong value={wrong?.revenue} right={r.linear_revenue} format={money} />
                  <span className="muted small"> vs {money(r.last_revenue)}</span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </>
  );
}

function Category({ result, fanout }: { result: QuestionResult; fanout: boolean }) {
  const rows = result.dashboard.rows as SkuRow[];
  const per = result.fanout.per_key;
  const max = Math.max(...rows.map((r) => r.impressions), 1);
  return (
    <>
      <div className="dash-head">
        <h2>SKU 漏斗：曝光 → 瀏覽 → 加購 → 下單</h2>
        <span className="legend"><i className="sw sw-flag" />看很多、轉換差</span>
      </div>
      <div className="funnel-scroll">
        <table className="funnel">
          <thead>
            <tr><th>SKU</th><th>品類</th><th className="bar-col">曝光</th><th>瀏覽</th><th>加購</th><th>下單</th><th>件數</th><th>瀏覽→下單</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.sku} className={r.flag ? "flag" : ""}>
                <td>{r.sku} {r.name}</td>
                <td>{r.cat}</td>
                <td className="bar-col">
                  <div className="bar-track"><div className="bar" style={{ width: `${(r.impressions / max) * 100}%` }} /></div>
                  <span className="num">{count(r.impressions)}</span>
                </td>
                <td className="num">{count(r.views)}</td>
                <td className="num">{count(r.adds)}</td>
                <td className="num">{count(r.orders)}</td>
                <td><Wrong value={fanout ? per[r.sku]?.units : undefined} right={r.units} format={count} /></td>
                <td className="num">{percent(r.view_to_order)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
