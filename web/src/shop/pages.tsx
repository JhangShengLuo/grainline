import { useState, type FormEvent } from "react";
import { Link, Navigate, useLocation, useNavigate, useParams } from "react-router-dom";

import { formatPrice, type Product } from "../api";
import { usePageView, useTrackOnce, useTracking } from "../tracking/TrackingProvider";
import { useCart } from "./CartContext";
import { useCatalog } from "./ShopLayout";

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function ProductGrid({ products }: { products: Product[] }) {
  return (
    <ul className="grid">
      {products.map((p) => (
        <li key={p.id}>
          <Link to={`/shop/p/${p.id}`} className="card">
            <div className="thumb" aria-hidden="true">{p.name.slice(-2)}</div>
            <div className="card-name">{p.name}</div>
            <div className="card-price">{formatPrice(p.price)}</div>
          </Link>
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
  usePageView("home");
  const catalog = useCatalog();
  if (!catalog) return <p className="muted">載入商品…</p>;
  return (
    <>
      <h1>本週精選</h1>
      <Categories catalog={catalog} />
      <ProductGrid products={catalog.slice(0, 12)} />
    </>
  );
}

export function CategoryPage() {
  usePageView("category");
  const { category = "" } = useParams();
  const catalog = useCatalog();
  if (!catalog) return <p className="muted">載入商品…</p>;
  return (
    <>
      <h1>{category}</h1>
      <Categories catalog={catalog} current={category} />
      <ProductGrid products={catalog.filter((p) => p.category === category)} />
    </>
  );
}

export function ProductPage() {
  usePageView("product");
  const { productId } = useParams();
  const location = useLocation();
  const catalog = useCatalog();
  const product = catalog?.find((p) => p.id === productId);
  const { tracker } = useTracking();
  const cart = useCart();
  const [quantity, setQuantity] = useState(1);
  const [status, setStatus] = useState<"idle" | "adding" | "added">("idle");

  // L1：商品詳情頁載入完成
  useTrackOnce("product_view", product ? { product_id: product.id, price: product.price } : null, location.key);

  if (!catalog) return <p className="muted">載入商品…</p>;
  if (!product) return <p>找不到商品 {productId}。<Link to="/shop">回首頁</Link></p>;

  async function addToCart() {
    if (!product) return;
    setStatus("adding");
    await sleep(300); // 假裝呼叫購物車 API
    cart.add({ productId: product.id, name: product.name, price: product.price, quantity });
    // L1：點擊「加入購物車」且 API 回傳成功後才送
    tracker.track("add_to_cart", { product_id: product.id, price: product.price, quantity });
    setStatus("added");
  }

  return (
    <article className="product">
      <div className="thumb large" aria-hidden="true">{product.name.slice(-2)}</div>
      <div className="product-info">
        <p className="muted">{product.category}・{product.id}</p>
        <h1>{product.name}</h1>
        <p className="price">{formatPrice(product.price)}</p>
        <label className="field">
          數量
          <select value={quantity} onChange={(e) => setQuantity(Number(e.target.value))}>
            {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        <div className="actions">
          <button type="button" className="primary" onClick={addToCart} disabled={status === "adding"}>
            {status === "adding" ? "加入中…" : "加入購物車"}
          </button>
          <button
            type="button"
            onClick={() => tracker.track("wishlist_add", { product_id: product.id })}
            title="wishlist_add 沒有在 L1 宣告，看看右側紀錄會怎麼樣"
          >
            ♡ 收藏
          </button>
        </div>
        {status === "added" && <p className="success">已加入購物車。<Link to="/shop/cart">去結帳</Link></p>}
      </div>
    </article>
  );
}

export function CartPage() {
  usePageView("cart");
  const cart = useCart();
  if (cart.lines.length === 0) return <><h1>購物車</h1><p className="muted">購物車是空的。<Link to="/shop">去逛逛</Link></p></>;
  return (
    <>
      <h1>購物車</h1>
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

interface OrderState {
  orderId: string;
  revenue: number;
  itemCount: number;
}

export function CheckoutPage() {
  usePageView("checkout");
  const location = useLocation();
  const navigate = useNavigate();
  const cart = useCart();
  const [paying, setPaying] = useState(false);
  // 進入結帳頁當下的購物車內容
  const [snapshot] = useState(() => ({ cart_value: cart.total, item_count: cart.count }));

  // L1：結帳頁載入完成
  useTrackOnce("checkout_start", cart.count > 0 ? snapshot : null, location.key);

  if (cart.lines.length === 0 && !paying) return <Navigate to="/shop/cart" replace />;

  async function pay() {
    setPaying(true);
    await sleep(500); // 假裝付款
    const order: OrderState = { orderId: `o-${crypto.randomUUID().slice(0, 8)}`, revenue: cart.total, itemCount: cart.count };
    cart.clear();
    navigate("/shop/complete", { state: order, replace: true });
  }

  return (
    <>
      <h1>結帳</h1>
      <CartTable />
      <p className="muted">這是 demo，不會真的扣款。</p>
      <div className="actions">
        <button type="button" className="primary" onClick={pay} disabled={paying}>
          {paying ? "付款中…" : `付款 ${formatPrice(cart.total)}`}
        </button>
      </div>
    </>
  );
}

const SENT_ORDERS_KEY = "grainline.sent_orders";

function alreadySent(orderId: string): boolean {
  const sent: string[] = JSON.parse(sessionStorage.getItem(SENT_ORDERS_KEY) ?? "[]");
  if (sent.includes(orderId)) return true;
  sessionStorage.setItem(SENT_ORDERS_KEY, JSON.stringify([...sent, orderId]));
  return false;
}

export function CompletePage() {
  const order = useLocation().state as OrderState | null;
  // L1：付款成功頁載入。重新整理這頁不能再送一次，否則同一張訂單會被算兩次（違反 fct_orders 的 grain）
  const [shouldSend] = useState(() => (order ? !alreadySent(order.orderId) : false));
  const properties = order && shouldSend
    ? { order_id: order.orderId, revenue: order.revenue, item_count: order.itemCount }
    : null;
  useTrackOnce("order_completed", properties, order?.orderId);

  if (!order) return <Navigate to="/shop" replace />;
  return (
    <>
      <h1>訂單成立</h1>
      <p>訂單編號 <code>{order.orderId}</code>，金額 {formatPrice(order.revenue)}，共 {order.itemCount} 件。</p>
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
    // L1：登入 API 回傳成功，之後的事件都會帶 user_id（所以先 identify 再送 login）
    login(`demo-${slug}`);
    tracker.track("login", { method });
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
      <button type="submit" className="primary">登入</button>
    </form>
  );
}
