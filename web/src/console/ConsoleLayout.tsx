import { Link, NavLink, Outlet, useLocation } from "react-router-dom";

import type { TrackingPlanSummary } from "../tracking/types";
import { usePlanParam, useApi } from "./api";

export function ConsoleLayout() {
  const [plan, setPlan] = usePlanParam();
  const plans = useApi<TrackingPlanSummary[]>("/tracking-plans");
  const { search, pathname } = useLocation();
  const current = plan ?? plans.data?.find((p) => p.default)?.version;
  const onDiff = pathname.endsWith("/diff");
  const keep = (to: string) => ({ pathname: to, search: plan ? `?plan=${plan}` : "" });

  return (
    <div className="console">
      <header className="shop-header">
        <Link to="/console" className="brand">Grainline Console</Link>
        <nav className="shop-nav">
          <NavLink to={keep("/console/reports")}>報表</NavLink>
          <NavLink to={keep("/console/checks")}>相容性檢查</NavLink>
          <NavLink to={{ pathname: "/console/diff", search: onDiff ? search : "" }}>版本差異</NavLink>
          <Link to="/shop">Demo 商店</Link>
        </nav>
        {!onDiff && plans.data && current !== undefined && (
          <label className="plan-select" title="報表與檢查依這個 tracking plan 計算">
            Tracking plan
            <select value={current} onChange={(e) => setPlan(Number(e.target.value))}>
              {plans.data.map((p) => (
                <option key={p.version} value={p.version}>
                  v{p.version} {p.name}{p.default ? "（現行）" : ""}
                </option>
              ))}
            </select>
          </label>
        )}
      </header>
      <main className="console-main">
        <Outlet />
      </main>
    </div>
  );
}
