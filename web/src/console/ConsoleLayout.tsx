import { Link, NavLink, Navigate, Outlet, useLocation } from "react-router-dom";

import type { TrackingPlanSummary } from "../tracking/types";
import { usePlanParam, useApi } from "./api";
import { PAGE_LABEL, ROLES, storedRole, useRole, type Page, type Role } from "./roles";

/** /console 依角色導到該角色的第一頁 */
export function ConsoleHome() {
  return <Navigate to={ROLES[storedRole()].pages[0]} replace />;
}

export function ConsoleLayout() {
  const [plan, setPlan] = usePlanParam();
  const [role, setRole] = useRole();
  const plans = useApi<TrackingPlanSummary[]>("/tracking-plans");
  const { search, pathname } = useLocation();
  const current = plan ?? plans.data?.find((p) => p.default)?.version;
  const onDiff = pathname.endsWith("/diff");
  const link = (page: Page) =>
    page === "diff"
      ? { pathname: "/console/diff", search: onDiff ? search : "" }
      : { pathname: `/console/${page}`, search: plan ? `?plan=${plan}` : "" };
  const mine: readonly Page[] = ROLES[role].pages;
  const others = (Object.keys(PAGE_LABEL) as Page[]).filter((p) => !mine.includes(p));

  return (
    <div className="console">
      <header className="shop-header">
        <Link to="/console" className="brand">Grainline Console</Link>
        <label className="plan-select" title="導覽列會顯示這個角色最常用的頁面">
          我是
          <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
            {(Object.keys(ROLES) as Role[]).map((r) => (
              <option key={r} value={r}>{ROLES[r].label}</option>
            ))}
          </select>
        </label>
        <nav className="shop-nav">
          {mine.map((page) => (
            <NavLink key={page} to={link(page)}>{PAGE_LABEL[page]}</NavLink>
          ))}
          <span className="nav-sep" aria-hidden="true" />
          {others.map((page) => (
            <NavLink key={page} to={link(page)} className="nav-other">{PAGE_LABEL[page]}</NavLink>
          ))}
          <Link to="/shop" className="nav-other">Demo 商店</Link>
        </nav>
        {!onDiff && plans.data && current !== undefined && (
          <label className="plan-select" title="這一頁依這個 tracking plan 計算">
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
