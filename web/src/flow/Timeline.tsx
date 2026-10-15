import { campaignText, EVENT_TEXT, MODEL_TEXT, money } from "../api";
import type { Journey, Model, Order, Product, Question, Step, Table } from "../types";
import { HitView, requiredFor } from "./HitView";
import { RowsView } from "./RowsView";
import { ShopPage } from "./ShopPage";

const HIT_COLUMNS = ["hit_id", "ev", "uid", "utm_campaign", "sku", "qty", "order_id", "value"];
const HIGHLIGHT: Record<string, string> = { list_view: "items", add_to_cart: "add_to_cart", purchase: "purchase", product_view: "product" };

export function Timeline({ journey, products, question, model, fanout, hover, shown, onOpen }: {
  journey: Journey;
  products: Record<string, Product>;
  question: Question;
  model: Model | null;
  fanout: boolean;
  hover: string | null;
  shown: number;
  onOpen: (step: Step) => void;
}) {
  const orders = new Map<number, Order[]>();
  for (const o of journey.orders) if (o.after_hit_id !== null) orders.set(o.after_hit_id, [...(orders.get(o.after_hit_id) ?? []), o]);
  let visit = 0;
  let campaign: string | null = null;
  return (
    <div className="timeline">
      {journey.steps.map((step, i) => {
        if (step.session_start) {
          visit += 1;
          campaign = step.campaign;
        }
        const future = i >= shown;
        const lit = hover !== null && (campaign ?? "(直接進站)") === hover;
        return (
          <div key={step.hit_id} className="step-group">
            <div className={`step ${future ? "future" : "arrived"} ${step.session_start ? "session-start" : ""} ${lit ? "lit" : ""}`}
              style={{ animationDelay: `${Math.min(i, 12) * 40}ms` }}>
              {step.session_start && (
                <div className={`visit ${step.campaign ? "visit-campaign" : ""}`}>
                  第 {visit} 次造訪 · {campaignText(step.campaign)}
                </div>
              )}
              <div className="step-head">
                <span className="time">{step.ts.slice(5, 16)}</span>
                <span className="ev">{EVENT_TEXT[step.ev] ?? step.ev}</span>
              </div>
              <button className="thumb" onClick={() => onOpen(step)} aria-label={`放大第 ${i + 1} 步的頁面`}>
                <div className="thumb-inner">
                  <ShopPage page={step.page} products={products} campaign={step.session_start ? step.campaign : null}
                    highlight={step.session_start && step.campaign ? "utm" : HIGHLIGHT[step.ev]} />
                </div>
              </button>
              <div className="flow-arrow" aria-hidden="true">↓ 送出</div>
              <HitView step={step} required={requiredFor(step.ev, question.requires)} />
              <div className="flow-arrow" aria-hidden="true">↓ 解析</div>
              {step.tables.map((t) => (
                <RowsView key={t.model} table={t} columns={t.model === "stg_hits" ? HIT_COLUMNS.filter((c) => c in t.rows[0]) : undefined} />
              ))}
              {step.ev === "purchase" && step.order_linked === false && (
                <div className="unlinked">
                  後端訂單接不回來：這筆 purchase 沒帶 <code>order</code>，訪客也沒有 <code>uid</code>
                </div>
              )}
            </div>
            {!future && (orders.get(step.hit_id) ?? []).map((o) => (
              <OrderCard key={o.order_id} order={o} model={model} fanout={fanout} touches={journey.touches} />
            ))}
          </div>
        );
      })}
    </div>
  );
}

function OrderCard({ order, model, fanout, touches }: { order: Order; model: Model | null; fanout: boolean; touches: Record<string, string>[] }) {
  const lines = order.tables.find((t) => t.model === "fct_order_lines")!;
  const credit = model ? (order.attribution[model] ?? []) : [];
  const before = touches; // 錯誤的 join 不看時間：這個人所有的觸點都接上來
  const exploded: Table = {
    model: "lines × touches",
    title: "錯誤 join",
    grain: "明細 × 觸點（不是訂單）",
    rows: lines.rows.flatMap((l) => before.map((t) => ({ order_id: order.order_id, sku: l.sku, campaign: t.campaign, total: order.total }))),
  };
  return (
    <div className="order">
      <div className="order-head">
        <span className="tag">後端</span> 訂單 {order.order_id} · {money(order.total)}
        <span className="muted small"> 用 {order.linked_by === "uid" ? "會員編號 uid" : "訂單編號 order"} 接回瀏覽紀錄</span>
      </div>
      {order.tables.map((t) => <RowsView key={t.model} table={t} />)}
      {model && (
        <div className="credit">
          <div className="credit-title">{MODEL_TEXT[model]}：這筆錢算給誰</div>
          <div className="credit-bar">
            {credit.map((c, i) => (
              <div key={i} className={`credit-seg c-${c.campaign.replace(/[^a-z_]/g, "") || "none"}`} style={{ flex: c.weight }}>
                {campaignText(c.campaign)} {money(c.revenue)}
              </div>
            ))}
          </div>
        </div>
      )}
      {fanout && exploded.rows.length > 0 && (
        <>
          <div className="explode">
            {lines.rows.length} 個明細 × {before.length} 個觸點 = <b>{exploded.rows.length} 列</b>，一張訂單被算成 {exploded.rows.length} 張、營收 {money(order.total * exploded.rows.length)}
          </div>
          <RowsView table={exploded} danger />
        </>
      )}
    </div>
  );
}
