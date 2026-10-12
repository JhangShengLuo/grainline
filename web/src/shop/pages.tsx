import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, Navigate, useLocation, useNavigate, useParams } from "react-router-dom";

import { formatPrice, type Product } from "../api";
import { useMomentOnce, usePageLoad, useTracking } from "../tracking/TrackingProvider";
import { PageMoments, TrackPoint } from "./TrackPoint";
import { useCart } from "./CartContext";
import { useCatalog } from "./ShopLayout";

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** 商品卡片進入畫面一半以上時回報一次曝光（每次頁面載入每張卡片最多一次） */
function ProductCard({ product, list, position }: { product: Product; list: string; position: number }) {
  const ref = useRef<HTMLAnchorElement>(null);
  const { tracker } = useTracking();
  const location = useLocation();
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry.isIntersecting) return;
        observer.disconnect();
        tracker.moment("product_impression", {
          product: { id: product.id, price: product.price },
          list: { name: list, position },
        });
      },
      { threshold: 0.5 },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [tracker, product.id, product.price, list, position, location.key]);

  return (
    <TrackPoint moments={["product_impression"]} label={`商品卡片（${list === "home_featured" ? "首頁精選" : "分類頁"}第 ${position} 個）`} block
      note="點進去會到商品頁（商品頁的埋點在頁面標題下面）">
      <Link ref={ref} to={`/shop/p/${product.id}`} className="card">
        <div className="thumb" aria-hidden="true">{product.name.slice(-2)}</div>
        <div className="card-name">{product.name}</div>
        <div className="card-price">{formatPrice(product.price)}</div>
      </Link>
    </TrackPoint>
  );
}

function ProductGrid({ products, list }: { products: Product[]; list: string }) {
  return (
    <ul className="grid">
      {products.map((p, i) => (
        <li key={p.id}>
          <ProductCard product={p} list={list} position={i + 1} />
        </li>
      ))}
    </ul>
  );
}

function Categories({ catalog, current }: { catalog: Product[]; current?: string }) {
  const categories = [...new Set(catalog.map((p) => p.category))];
  return (
    <nav className="chips" aria-label="分類">
      {categories.map((c) => (
        <Link key={c} to={`/shop/c/${encodeURIComponent(c)}`} aria-current={c === current ? "page" : undefined}>
          {c}
        </Link>
      ))}
    </nav>
  );
}

export function HomePage() {
  usePageLoad("home");
  const catalog = useCatalog();
  if (!catalog) return <p className="muted">載入商品…</p>;
  return (
    <>
      <h1>本週精選</h1>
      <PageMoments moments={["page_load"]} label="首頁載入" />
      <Categories catalog={catalog} />
      <ProductGrid
        list="home_featured"
        products={catalog.filter((p) => p.featured_rank).sort((a, b) => a.featured_rank! - b.featured_rank!)}
      />
    </>
  );
}

export function CategoryPage() {
  usePageLoad("category");
  const { category = "" } = useParams();
  const catalog = useCatalog();
  if (!catalog) return <p className="muted">載入商品…</p>;
  return (
    <>
      <h1>{category}</h1>
      <PageMoments moments={["page_load"]} label="分類頁載入" />
      <Categories catalog={catalog} current={category} />
      <ProductGrid list="category" products={catalog.filter((p) => p.category === category)} />
    </>
  );
}

export function ProductPage() {
  const { productId } = useParams();
  const location = useLocation();
  const catalog = useCatalog();
  const product = catalog?.find((p) => p.id === productId);
  const { tracker } = useTracking();
  const cart = useCart();
  const [quantity, setQuantity] = useState(1);
  const [status, setStatus] = useState<"idle" | "adding" | "added" | "failed">("idle");

  // 商品資料到了才算頁面載入完成；要送 page_view 還是 product_view、帶哪些欄位，由 tracking plan 決定
  const productContext = product ? { product: { id: product.id, price: product.price } } : null;
  usePageLoad("product", productContext);
  useMomentOnce("product_detail_load", productContext, location.key);

  if (!catalog) return <p className="muted">載入商品…</p>;
  if (!product) return <p>找不到商品 {productId}。<Link to="/shop">回首頁</Link></p>;

  async function addToCart() {
    if (!product) return;
    const context = { product: { id: product.id, price: product.price }, item: { quantity } };
    tracker.moment("add_to_cart_click", context);
    setStatus("adding");
    await sleep(300); // 假裝呼叫購物車 API，和模擬器一樣有 10% 會失敗
    if (Math.random() < 0.1) {
      setStatus("failed");
      return;
    }
    cart.add({ productId: product.id, name: product.name, price: product.price, quantity });
    tracker.moment("add_to_cart_success", {
      ...context,
      cart: { total: cart.total + product.price * quantity, count: cart.count + quantity },
    });
    setStatus("added");
  }

  return (
    <article className="product">
      <div className="thumb large" aria-hidden="true">{product.name.slice(-2)}</div>
      <div className="product-info">
        <p className="muted">{product.category}・{product.id}</p>
        <h1>{product.name}</h1>
        <PageMoments moments={["page_load", "product_detail_load"]} label="商品頁載入" />
        <p className="price">{formatPrice(product.price)}</p>
        <label className="field">
          數量
          <select value={quantity} onChange={(e) => setQuantity(Number(e.target.value))}>
            {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        <div className="actions">
          <TrackPoint moments={["add_to_cart_click", "add_to_cart_success"]} label="加入購物車按鈕"
            note="按下就是 add_to_cart_click；購物車 API 成功才是 add_to_cart_success（10% 會失敗）">
            <button type="button" className="primary" onClick={addToCart} disabled={status === "adding"}>
              {status === "adding" ? "加入中…" : "加入購物車"}
            </button>
          </TrackPoint>
          <TrackPoint moments={[]} custom="wishlist_add" label="收藏按鈕">
            <button
              type="button"
              onClick={() => tracker.track("wishlist_add", { product_id: product.id })}
              title="wishlist_add 沒有在 L1 宣告，看看右側紀錄會怎麼樣"
            >
              ♡ 收藏
            </button>
          </TrackPoint>
        </div>
        {status === "added" && <p className="success">已加入購物車。<Link to="/shop/cart">去結帳</Link></p>}
        {status === "failed" && <p className="warning-text">庫存同步逾時，請再試一次。（模擬 API 失敗：看看兩個版本的 add_to_cart 各記錄了什麼）</p>}
      </div>
    </article>
  );
}

export function CartPage() {
  usePageLoad("cart");
  const cart = useCart();
  if (cart.lines.length === 0) return <><h1>購物車</h1><p className="muted">購物車是空的。<Link to="/shop">去逛逛</Link></p></>;
  return (
    <>
      <h1>購物車</h1>
      <PageMoments moments={["page_load"]} label="購物車頁載入" />
      <CartTable />
      <div className="actions">
        <Link to="/shop/checkout" className="button primary">前往結帳</Link>
      </div>
    </>
  );
}

function CartTable() {
  const cart = useCart();
  return (
    <table className="cart">
      <tbody>
        {cart.lines.map((l) => (
          <tr key={l.productId}>
            <td>{l.name}</td>
            <td className="num">× {l.quantity}</td>
            <td className="num">{formatPrice(l.price * l.quantity)}</td>
          </tr>
        ))}
      </tbody>
      <tfoot>
        <tr>
          <td>合計</td>
          <td className="num">{cart.count} 件</td>
          <td className="num">{formatPrice(cart.total)}</td>
        </tr>
      </tfoot>
    </table>
  );
}

interface BackendOrder {
  order_id: string;
  subtotal: number;
  discount_amount: number;
  shipping_fee: number;
  total: number;
  coupon: { code: string; accepted: boolean; message: string } | null;
}

interface OrderState {
  orderId: string;
  couponCode: string | null;
  /** 前端回報的金額：購物車金額（未扣折扣、不含運費） */
  revenue: number;
  itemCount: number;
  backend: BackendOrder;
}

const orderContext = (order: OrderState) => ({
  order: { id: order.orderId, coupon_code: order.couponCode },
  cart: { total: order.revenue, count: order.itemCount },
});

export function CheckoutPage() {
  usePageLoad("checkout");
  const location = useLocation();
  const navigate = useNavigate();
  const cart = useCart();
  const { tracker, platform } = useTracking();
  const [paying, setPaying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [coupon, setCoupon] = useState("");
  // 進入結帳頁當下的購物車內容
  const [snapshot] = useState(() => ({ cart: { total: cart.total, count: cart.count } }));

  useMomentOnce("checkout_load", cart.count > 0 ? snapshot : null, location.key);

  if (cart.lines.length === 0 && !paying) return <Navigate to="/shop/cart" replace />;

  async function pay() {
    setPaying(true);
    setError(null);
    // 後端建立訂單：計價、驗證折扣碼、算運費。訂單以後端為準，前端事件只是回報
    const response = await fetch("/api/shop/orders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        anonymous_id: tracker.anonymousId,
        user_id: tracker.userId,
        platform,
        lines: cart.lines.map((l) => ({ product_id: l.productId, quantity: l.quantity })),
        coupon_code: coupon.trim() || null,
      }),
    });
    if (!response.ok) {
      setPaying(false);
      setError(`後端建立訂單失敗（HTTP ${response.status}）`);
      return;
    }
    const backend: BackendOrder = await response.json();
    await sleep(300); // 假裝付款
    const order: OrderState = {
      orderId: backend.order_id,
      couponCode: backend.coupon?.accepted ? backend.coupon.code : null,
      revenue: cart.total,
      itemCount: cart.count,
      backend,
    };
    // 付款成功；接著才導到成功頁（使用者可能沒等到，也可能重新整理）
    tracker.moment("payment_success", orderContext(order));
    cart.clear();
    navigate("/shop/complete", { state: order, replace: true });
  }

  return (
    <>
      <h1>結帳</h1>
      <PageMoments moments={["page_load", "checkout_load"]} label="結帳頁載入" />
      <CartTable />
      <label className="field">
        折扣碼（選填）
        <input value={coupon} onChange={(e) => setCoupon(e.target.value)} placeholder="FALL10" />
      </label>
      <p className="muted">這是 demo，不會真的扣款。</p>
      <div className="actions">
        <TrackPoint moments={["payment_success"]} label="付款按鈕"
          note="按下後由後端建立訂單（orders 表）：訂單數、營收以後端為準；前端事件只是回報">
          <button type="button" className="primary" onClick={pay} disabled={paying}>
            {paying ? "付款中…" : `付款 ${formatPrice(cart.total)}`}
          </button>
        </TrackPoint>
      </div>
      {error && <p className="warning-text">{error}</p>}
    </>
  );
}

export function CompletePage() {
  const location = useLocation();
  const order = location.state as OrderState | null;
  // 每次載入都是一次 order_complete_page_load，重新整理也算——這是真實世界的行為。
  // v1 在 payment_success 記訂單，不受影響；v2 在這個時刻記訂單，重新整理就會重複。
  useMomentOnce("order_complete_page_load", order && orderContext(order), location.key);

  if (!order) return <Navigate to="/shop" replace />;
  return (
    <>
      <h1>訂單成立</h1>
      <PageMoments moments={["order_complete_page_load"]} label="付款成功頁載入" />
      <p>訂單編號 <code>{order.orderId}</code>，共 {order.itemCount} 件。</p>
      <table className="cart">
        <tbody>
          <tr><td>商品金額</td><td className="num">{formatPrice(order.backend.subtotal)}</td></tr>
          <tr><td>折扣{order.couponCode && <>（<code>{order.couponCode}</code>）</>}</td><td className="num">−{formatPrice(order.backend.discount_amount)}</td></tr>
          <tr><td>運費</td><td className="num">{formatPrice(order.backend.shipping_fee)}</td></tr>
        </tbody>
        <tfoot><tr><td>實付</td><td className="num">{formatPrice(order.backend.total)}</td></tr></tfoot>
      </table>
      {order.backend.coupon && !order.backend.coupon.accepted && (
        <p className="warning-text">折扣碼 {order.backend.coupon.code} 沒有套用：{order.backend.coupon.message}</p>
      )}
      <p className="muted small">試試重新整理這一頁，再看右側的埋點紀錄：v1 和 v2 的結果不一樣。</p>
      <p><Link to="/shop">繼續逛逛</Link></p>
    </>
  );
}

export function LoginPage() {
  const { plan, login, tracker } = useTracking();
  const navigate = useNavigate();
  const methods = plan.events.login?.properties?.method?.enum ?? ["email"];
  const [name, setName] = useState("");
  const [method, setMethod] = useState(methods[0]);

  function submit(event: FormEvent) {
    event.preventDefault();
    const slug = name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-") || "guest";
    // 登入 API 回傳成功：先 identify，之後的事件（包括 login 本身）都帶 user_id
    login(`demo-${slug}`);
    tracker.moment("login_success", { login: { method } });
    navigate(-1);
  }

  return (
    <form className="login" onSubmit={submit}>
      <h1>登入</h1>
      <label className="field">
        名稱（英數字，會變成 user_id）
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="amy" required />
      </label>
      <label className="field">
        登入方式
        <select value={method} onChange={(e) => setMethod(e.target.value)}>
          {methods.map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
      </label>
      <p className="muted">登入前在這個裝置的瀏覽紀錄，L2 會回溯算到同一個人身上。</p>
      <TrackPoint moments={["login_success"]} label="登入按鈕" note="登入成功後，之後的事件都帶 user_id；登入前的瀏覽也會回溯算到同一個人">
        <button type="submit" className="primary">登入</button>
      </TrackPoint>
    </form>
  );
}
