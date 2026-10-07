import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useOutletContext } from "react-router-dom";

import { api, type Product } from "../api";
import { useTracking, type Platform } from "../tracking/TrackingProvider";
import { useCart } from "./CartContext";
import { EventLog } from "./EventLog";

export function useCatalog(): Product[] | null {
  return useOutletContext<Product[] | null>();
}

export function ShopLayout() {
  const [catalog, setCatalog] = useState<Product[] | null>(null);
  const { platform, setPlatform, userId, logout, plans, planVersion, setPlanVersion } = useTracking();
  const cart = useCart();

  useEffect(() => {
    api<Product[]>("/catalog").then(setCatalog, () => setCatalog([]));
  }, []);

  return (
    <div className="shop">
      <header className="shop-header">
        <Link to="/shop" className="brand">Grainline Shop</Link>
        <nav className="shop-nav">
          <NavLink to="/shop" end>首頁</NavLink>
          <NavLink to="/shop/cart">購物車{cart.count > 0 && <span className="badge">{cart.count}</span>}</NavLink>
          <Link to="/console">Console</Link>
          {userId ? (
            <button type="button" className="link" onClick={logout} title="登出後事件不再帶 user_id">
              {userId}・登出
            </button>
          ) : (
            <NavLink to="/shop/login">登入</NavLink>
          )}
        </nav>
        <label className="plan-select" title="切換後，同樣的操作會依新的 tracking plan 送出不同的事件">
          埋點設計
          <select value={planVersion} onChange={(e) => setPlanVersion(Number(e.target.value))}>
            {plans.map((p) => (
              <option key={p.version} value={p.version}>
                v{p.version} {p.name}
              </option>
            ))}
          </select>
        </label>
        <div className="segmented" role="group" aria-label="模擬裝置" title="每個平台是不同裝置，各有自己的 anonymous_id">
          {(["web", "app"] as Platform[]).map((p) => (
            <button key={p} type="button" aria-pressed={platform === p} onClick={() => setPlatform(p)}>
              {p === "web" ? "Web" : "App"}
            </button>
          ))}
        </div>
      </header>
      <div className="shop-body">
        <main className="shop-main">
          <Outlet context={catalog} />
        </main>
        <EventLog />
      </div>
    </div>
  );
}
