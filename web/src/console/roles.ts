import { useCallback, useState } from "react";

export type Page = "export" | "story" | "decisions" | "assistant" | "metrics" | "library" | "reports" | "checks" | "diff" | "events" | "models";

export const PAGE_LABEL: Record<Page, string> = {
  story: "為什麼",
  export: "標準匯出",
  decisions: "決策",
  assistant: "AI 提案",
  metrics: "指標說明",
  library: "指標庫",
  reports: "報表",
  checks: "相容性檢查",
  diff: "版本差異",
  events: "埋點清單",
  models: "資料模型",
};

export const ROLES = {
  biz: { label: "業務／行銷", pages: ["decisions", "assistant", "metrics", "library", "reports"] },
  bi: { label: "BI", pages: ["reports", "decisions", "assistant", "metrics", "library", "checks", "diff", "export"] },
  fe: { label: "前端／PM", pages: ["events", "library", "diff", "checks"] },
  de: { label: "DE", pages: ["models", "library", "checks", "export", "diff", "reports"] },
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
