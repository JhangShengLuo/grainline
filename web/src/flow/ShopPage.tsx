import type { PageInfo, Product, ShopAction } from "../types";
import "./shop.css";

export interface ShopPageProps {
  page: PageInfo;
  products: Record<string, Product>;
  interactive?: boolean;
  highlight?: string;
  campaign?: string | null;
  onAction?: (a: ShopAction) => void;
}

const SOURCES: { label: string; campaign: string | null }[] = [
  { label: "FB 廣告", campaign: "xmas_fb" },
  { label: "LINE", campaign: "xmas_line" },
  { label: "Google", campaign: "xmas_gg" },
  { label: "EDM", campaign: "xmas_edm" },
  { label: "直接進站", campaign: null },
];

const CAMPAIGN_LABEL: Record<string, string> = {
  xmas_fb: "從 FB 廣告進站",
  xmas_line: "從 LINE 進站",
  xmas_gg: "從 Google 廣告進站",
  xmas_edm: "從 EDM 進站",
};

function catColor(cat: string): string {
  let h = 0;
  for (let i = 0; i < cat.length; i++) h = (h * 31 + cat.charCodeAt(i)) >>> 0;
  return `hsl(${h % 360} 40% 62%)`;
}

const price = (n: number) => `NT$ ${n.toLocaleString("zh-TW")}`;

export function ShopPage({
  page,
  products,
  interactive = false,
  highlight,
  campaign,
  onAction,
}: ShopPageProps) {
  const act = (a: ShopAction) => {
    if (interactive && onAction) onAction(a);
  };
  const hl = (name: string) => (highlight === name ? " shop-hl" : "");

  const gridItems = (): { sku: string; cat: string; price: number }[] => {
    if (page.items && page.items.length) return page.items;
    return Object.values(products)
      .slice(0, 6)
      .map((p) => ({ sku: p.sku, cat: p.cat, price: p.price }));
  };

  const grid = () => (
    <div className={"shop-grid" + hl("items")}>
      {gridItems().map((it) => {
        const p = products[it.sku];
        return (
          <button
            key={it.sku}
            type="button"
            className="shop-card"
            onClick={() => act({ action: "product", sku: it.sku })}
          >
            <span className="shop-ph" style={{ background: catColor(it.cat) }} />
            <span className="shop-card-name">{p?.name ?? it.sku}</span>
            <span className="shop-card-price">{price(it.price)}</span>
          </button>
        );
      })}
    </div>
  );

  const ribbon =
    campaign && (page.kind === "campaign" || page.kind === "home") ? (
      <div className={"shop-ribbon" + hl("utm")}>
        {CAMPAIGN_LABEL[campaign] ?? `從 ${campaign} 進站`}
      </div>
    ) : null;

  let body: React.ReactNode;
  switch (page.kind) {
    case "campaign":
    case "home":
      body = (
        <>
          {ribbon}
          <div className="shop-hero">
            {page.kind === "campaign" ? "聖誕檔期 全館 85 折" : "精選商品"}
          </div>
          {grid()}
        </>
      );
      break;
    case "category":
      body = (
        <>
          <div className="shop-title">{page.cat ?? "商品"}</div>
          {grid()}
        </>
      );
      break;
    case "product": {
      const p = page.sku ? products[page.sku] : undefined;
      body = (
        <div className="shop-detail">
          <div
            className="shop-ph shop-ph-big"
            style={{ background: catColor(p?.cat ?? page.cat ?? "") }}
          />
          <div className={"shop-info" + hl("product")}>
            <div className="shop-name">{p?.name ?? page.sku ?? "商品"}</div>
            <div className="shop-cat">{p?.cat ?? page.cat ?? ""}</div>
            <div className="shop-price">{p ? price(p.price) : ""}</div>
          </div>
          <div className="shop-qty">
            數量 <span className="shop-qty-box">1</span>
          </div>
          <button
            type="button"
            className={"shop-btn shop-btn-primary" + hl("add_to_cart")}
            onClick={() => page.sku && act({ action: "add", sku: page.sku })}
          >
            加入購物車
          </button>
          <button
            type="button"
            className="shop-btn shop-btn-secondary"
            onClick={() => act({ action: "purchase" })}
          >
            去結帳
          </button>
        </div>
      );
      break;
    }
    case "checkout": {
      const p = page.sku ? products[page.sku] : undefined;
      body = (
        <div className="shop-detail">
          <div className="shop-title">購物車</div>
          <div className="shop-cart-row">
            <span className="shop-ph shop-ph-sm" style={{ background: catColor(p?.cat ?? "") }} />
            <span className="shop-cart-name">{p?.name ?? "商品"}</span>
            <span className="shop-cart-price">{p ? price(p.price) : ""}</span>
          </div>
          <button
            type="button"
            className="shop-btn shop-btn-primary"
            onClick={() => act({ action: "purchase" })}
          >
            付款
          </button>
        </div>
      );
      break;
    }
    case "complete":
      body = (
        <div className="shop-done">
          <div className={"shop-check" + hl("purchase")}>✓</div>
          <div className="shop-name">訂單完成</div>
          <div className="shop-cat">感謝購買</div>
          <button
            type="button"
            className="shop-btn shop-btn-secondary"
            onClick={() => act({ action: "list" })}
          >
            繼續逛
          </button>
        </div>
      );
      break;
  }

  const showSources = interactive && (page.kind === "campaign" || page.kind === "home");

  return (
    <div className={"shop-wrap" + (interactive ? "" : " shop-static")}>
      {showSources && (
        <div className="shop-sources">
          <span className="shop-sources-label">進站來源</span>
          {SOURCES.map((s) => (
            <button
              key={s.label}
              type="button"
              className={"shop-src" + (s.campaign === (campaign ?? null) ? " is-on" : "")}
              onClick={() => act({ action: "land", campaign: s.campaign })}
            >
              {s.label}
            </button>
          ))}
        </div>
      )}
      <div className="shop-phone">
        <div className="shop-bar">
          <span className="shop-logo">冬日選物</span>
          <span className="shop-cart" aria-label="購物車">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="9" cy="20" r="1.5" />
              <circle cx="18" cy="20" r="1.5" />
              <path d="M2 3h3l2.5 12h11L21 7H6" />
            </svg>
          </span>
        </div>
        <div className="shop-body">{body}</div>
      </div>
    </div>
  );
}
