import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";

import { api } from "../api";
import { fetchTransport } from "./browser";
import { Tracker } from "./tracker";
import type { Properties, TrackedEvent, TrackingPlan } from "./types";

export type Platform = "web" | "app";

interface TrackingContextValue {
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

export function TrackingProvider({ children }: { children: ReactNode }) {
  const [plan, setPlan] = useState<TrackingPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [platform, setPlatform] = useState<Platform>("web");
  const [log, setLog] = useState<TrackedEvent[]>([]);
  const [identityVersion, setIdentityVersion] = useState(0);

  useEffect(() => {
    api<TrackingPlan>("/tracking-plan").then(setPlan, (e: Error) => setError(e.message));
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
      plan && tracker
        ? { plan, tracker, platform, setPlatform, userId: tracker.userId, login, logout, log }
        : null,
    // identityVersion：登入登出後讓 userId 重新讀取
    [plan, tracker, platform, login, logout, log, identityVersion],
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

/** 每個 key 只送一次（例如每次路由切換一次）；properties 為 null 時等資料準備好再送 */
export function useTrackOnce(eventName: string, properties: Properties | null, key: string | undefined) {
  const { tracker } = useTracking();
  const fired = useRef<string | null>(null);
  useEffect(() => {
    if (!properties || key === undefined || fired.current === key) return;
    fired.current = key;
    tracker.track(eventName, properties);
  });
}

/** L1：page_view 在頁面載入完成時送出（SPA 切換路由也算） */
export function usePageView(pageType: string) {
  const location = useLocation();
  useTrackOnce("page_view", { page_type: pageType }, location.key);
}
