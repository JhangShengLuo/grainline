import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

export interface CartLine {
  productId: string;
  name: string;
  price: number;
  quantity: number;
}

interface CartValue {
  lines: CartLine[];
  total: number;
  count: number;
  add: (line: CartLine) => void;
  clear: () => void;
}

const CartContext = createContext<CartValue | null>(null);
const STORAGE_KEY = "grainline.cart";

function loadCart(): CartLine[] {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]");
  } catch {
    return [];
  }
}

export function CartProvider({ children }: { children: ReactNode }) {
  const [lines, setLines] = useState<CartLine[]>(loadCart);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(lines));
    } catch {
      // 無痕模式等情況存不了也沒關係
    }
  }, [lines]);

  const add = useCallback((line: CartLine) => {
    setLines((prev) => {
      const existing = prev.find((l) => l.productId === line.productId);
      if (!existing) return [...prev, line];
      return prev.map((l) => (l === existing ? { ...l, quantity: l.quantity + line.quantity } : l));
    });
  }, []);
  const clear = useCallback(() => setLines([]), []);

  const value = useMemo(
    () => ({
      lines,
      total: lines.reduce((sum, l) => sum + l.price * l.quantity, 0),
      count: lines.reduce((sum, l) => sum + l.quantity, 0),
      add,
      clear,
    }),
    [lines, add, clear],
  );
  return <CartContext.Provider value={value}>{children}</CartContext.Provider>;
}

export function useCart(): CartValue {
  const value = useContext(CartContext);
  if (!value) throw new Error("useCart 必須在 CartProvider 裡使用");
  return value;
}
