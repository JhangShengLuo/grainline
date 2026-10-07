import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";

import { api } from "../api";
import { fetchTransport } from "./browser";
import { Tracker } from "./tracker";
import type { Moment, MomentContext, TrackedEvent, TrackingPlan, TrackingPlanSummary } from "./types";

export type Platform = "web" | "app";

interface TrackingContextValue {
  plans: TrackingPlanSummary[];
  planVersion: number;
  setPlanVersion: (version: number) => void;
  plan: TrackingPlan;
  tracker: Tracker;
  platform: Platform;
  setPlatform: (platform: Platform) => void;
  userId: string | null;
  login: (userId: string) => void;
  logout: () => void;
  log: TrackedEvent[];
}

const TrackingContext = createContext<TrackingContextValue | null>(null);
const LOG_LIMIT = 200;
const PLAN_KEY = "grainline.shop.plan";

function storedPlanVersion(): number | null {
  try {
    const value = Number(localStorage.getItem(PLAN_KEY));
    return Number.isInteger(value) && value > 0 ? value : null;
  } catch {
    return null;
  }
}

export function TrackingProvider({ children }: { children: ReactNode }) {
  const [plans, setPlans] = useState<TrackingPlanSummary[] | null>(null);
  const [planVersion, setPlanVersionState] = useState<number | null>(storedPlanVersion);
  const [plan, setPlan] = useState<TrackingPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [platform, setPlatform] = useState<Platform>("web");
  const [log, setLog] = useState<TrackedEvent[]>([]);
  const [identityVersion, setIdentityVersion] = useState(0);

  useEffect(() => {
    api<TrackingPlanSummary[]>("/tracking-plans").then(
      (list) => {
        setPlans(list);
        // 記住的版本已經不存在時，退回 current
        setPlanVersionState((v) => (v && list.some((p) => p.version === v) ? v : list.find((p) => p.default)!.version));
      },
      (e: Error) => setError(e.message),
    );
  }, []);

  useEffect(() => {
    if (planVersion === null) return;
    // 新的 plan 到之前沿用舊的，畫面不會閃
    api<TrackingPlan>(`/tracking-plan?plan=${planVersion}`).then(setPlan, (e: Error) => setError(e.message));
  }, [planVersion]);

  const setPlanVersion = useCallback((version: number) => {
    try {
      localStorage.setItem(PLAN_KEY, String(version));
    } catch {
      // 存不了就只在這個分頁有效
    }
    setPlanVersionState(version);
  }, []);

  // 每個平台一個 tracker，各自有 anonymous_id，就像兩台不同的裝置
  const trackers = useMemo(() => {
    if (!plan) return null;
    const make = (p: Platform) => new Tracker({ platform: p, plan, transport: fetchTransport(), storage: localStorage });
    return { web: make("web"), app: make("app") };
  }, [plan]);

  useEffect(() => {
    if (!trackers) return;
    const record = (tracked: TrackedEvent) =>
      setLog((prev) => {
        const copy = { ...tracked };
        const rest = prev.filter((t) => t.event.event_id !== copy.event.event_id);
        return [copy, ...rest].slice(0, LOG_LIMIT);
      });
    const unsubscribe = [trackers.web.subscribe(record), trackers.app.subscribe(record)];
    const onPageHide = () => {
      trackers.web.flushWithBeacon();
      trackers.app.flushWithBeacon();
    };
    window.addEventListener("pagehide", onPageHide);
    return () => {
      // 換 plan 時，舊 tracker 還沒送的事件先送出
      void trackers.web.flush();
      void trackers.app.flush();
      unsubscribe.forEach((u) => u());
      window.removeEventListener("pagehide", onPageHide);
    };
  }, [trackers]);

  const tracker = trackers?.[platform];
  const login = useCallback(
    (userId: string) => {
      tracker?.identify(userId);
      setIdentityVersion((v) => v + 1);
    },
    [tracker],
  );
  const logout = useCallback(() => {
    tracker?.logout();
    setIdentityVersion((v) => v + 1);
  }, [tracker]);

  const value = useMemo(
    () =>
      plans && planVersion !== null && plan && tracker
        ? { plans, planVersion, setPlanVersion, plan, tracker, platform, setPlatform, userId: tracker.userId, login, logout, log }
        : null,
    // identityVersion：登入登出後讓 userId 重新讀取
    [plans, planVersion, setPlanVersion, plan, tracker, platform, login, logout, log, identityVersion],
  );

  if (error) return <div className="notice">無法讀取 L1 事件契約：{error}。API 有啟動嗎？</div>;
  if (!value) return <div className="notice">載入 L1 事件契約…</div>;
  return <TrackingContext.Provider value={value}>{children}</TrackingContext.Provider>;
}

export function useTracking(): TrackingContextValue {
  const value = useContext(TrackingContext);
  if (!value) throw new Error("useTracking 必須在 TrackingProvider 裡使用");
  return value;
}

/**
 * 每個 key 只回報一次行為時刻（例如每次路由切換一次）；context 為 null 時等資料準備好再回報。
 * 要送哪些事件由目前的 tracking plan 決定。
 */
export function useMomentOnce(moment: Moment, context: MomentContext | null, key: string | undefined) {
  const { tracker } = useTracking();
  const fired = useRef<string | null>(null);
  useEffect(() => {
    if (!context || key === undefined || fired.current === key) return;
    fired.current = key;
    tracker.moment(moment, context);
  });
}

/** 頁面載入完成（SPA 切換路由也算）；商品頁會帶上商品，讓 L1 決定要不要記錄 */
export function usePageLoad(pageType: string, extra: MomentContext | null = {}) {
  const location = useLocation();
  useMomentOnce("page_load", extra && { page: { type: pageType }, ...extra }, location.key);
}
