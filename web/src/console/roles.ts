import { useCallback, useState } from "react";

export type Page = "metrics" | "reports" | "checks" | "diff" | "events" | "models";

export const PAGE_LABEL: Record<Page, string> = {
  metrics: "指標說明",
  reports: "報表",
  checks: "相容性檢查",
  diff: "版本差異",
  events: "埋點清單",
  models: "資料模型",
};

export const ROLES = {
  biz: { label: "業務／行銷", pages: ["metrics", "reports"] },
  bi: { label: "BI", pages: ["reports", "metrics", "checks", "diff"] },
  fe: { label: "前端／PM", pages: ["events", "diff", "checks"] },
  de: { label: "DE", pages: ["models", "checks", "diff", "reports"] },
} as const satisfies Record<string, { label: string; pages: readonly Page[] }>;

export type Role = keyof typeof ROLES;
const KEY = "grainline.role";

export function storedRole(): Role {
  try {
    const value = localStorage.getItem(KEY);
    return value && value in ROLES ? (value as Role) : "biz";
  } catch {
    return "biz";
  }
}

export function useRole(): [Role, (role: Role) => void] {
  const [role, setRoleState] = useState<Role>(storedRole);
  const setRole = useCallback((next: Role) => {
    try {
      localStorage.setItem(KEY, next);
    } catch {
      // 存不了就只在這個分頁有效
    }
    setRoleState(next);
  }, []);
  return [role, setRole];
}
