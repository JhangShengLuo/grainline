import { Navigate, Route, Routes } from "react-router-dom";

import { ChecksPage } from "./console/ChecksPage";
import { ConsoleLayout } from "./console/ConsoleLayout";
import { DiffPage } from "./console/DiffPage";
import { ReportsPage } from "./console/ReportsPage";
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
      <Route path="/console" element={<ConsoleLayout />}>
        <Route index element={<Navigate to="reports" replace />} />
        <Route path="reports" element={<ReportsPage />} />
        <Route path="checks" element={<ChecksPage />} />
        <Route path="diff" element={<DiffPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/shop" replace />} />
    </Routes>
  );
}
