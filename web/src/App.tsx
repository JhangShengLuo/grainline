import { Link, Navigate, Route, Routes } from "react-router-dom";

import { CartProvider } from "./shop/CartContext";
import { ShopLayout } from "./shop/ShopLayout";
import {
  CartPage,
  CategoryPage,
  CheckoutPage,
  CompletePage,
  HomePage,
  LoginPage,
  ProductPage,
} from "./shop/pages";
import { TrackingProvider } from "./tracking/TrackingProvider";

function ConsolePlaceholder() {
  return (
    <div className="notice">
      <h1>Console</h1>
      <p>報表、相容性檢查與 tracking plan 版本切換會在 M4 放在這裡。</p>
      <p>目前可以直接看 API：<a href="/api/checks">/api/checks</a>、<a href="/api/reports/daily_overview">/api/reports/daily_overview</a></p>
      <p><Link to="/shop">去 demo 商店</Link></p>
    </div>
  );
}

export function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/shop" replace />} />
      <Route
        path="/shop"
        element={
          <TrackingProvider>
            <CartProvider>
              <ShopLayout />
            </CartProvider>
          </TrackingProvider>
        }
      >
        <Route index element={<HomePage />} />
        <Route path="c/:category" element={<CategoryPage />} />
        <Route path="p/:productId" element={<ProductPage />} />
        <Route path="cart" element={<CartPage />} />
        <Route path="checkout" element={<CheckoutPage />} />
        <Route path="complete" element={<CompletePage />} />
        <Route path="login" element={<LoginPage />} />
      </Route>
      <Route path="/console" element={<ConsolePlaceholder />} />
      <Route path="*" element={<Navigate to="/shop" replace />} />
    </Routes>
  );
}
