import { Link } from "react-router-dom";

import { planQuery, usePlanParam, useApi } from "./api";

type Status = "available" | "differs" | "buildable" | "blocked";

interface Missing {
  kind: string;
  owner: string;
  what: string;
}

interface LibraryMetric {
  name: string;
  group: string;
  group_label: string;
  label: string;
  definition: string;
  why: string;
  reference: string;
  grain: string;
  defined: boolean;
  broken: string | null;
  status: Status;
  missing: Missing[];
}

interface LibraryData {
  tracking_plan_version: number;
  groups: Record<string, string>;
  summary: Record<Status, number>;
  metrics: LibraryMetric[];
}

const STATUS: Record<Status, { icon: string; label: string; hint: string }> = {
  available: { icon: "✓", label: "已可用", hint: "資料齊全，專案也照指標庫的定義算" },
  differs: { icon: "!", label: "定義不同", hint: "算得出來，但做法和指標庫的定義不一樣" },
  buildable: { icon: "＋", label: "資料夠，還沒定義", hint: "需要的資料都有，在 L3／L4 加上定義就能用" },
  blocked: { icon: "✕", label: "缺資料", hint: "需要的事件或業務資料還沒有" },
};

export function LibraryPage() {
  const [plan] = usePlanParam();
  const { data, error } = useApi<LibraryData>(`/library${planQuery(plan)}`);
  if (error) return <p className="warning-text">{error.message}</p>;
  if (!data) return <p className="muted">載入指標庫…</p>;

  // 缺的東西依擁有者彙整：一眼看出要找誰
  const byOwner = new Map<string, Set<string>>();
  for (const m of data.metrics) {
    for (const miss of m.missing) {
      if (!byOwner.has(miss.owner)) byOwner.set(miss.owner, new Set());
      byOwner.get(miss.owner)!.add(miss.what);
    }
  }

  return (
    <>
      <h1>行銷指標庫</h1>
      <p className="muted">
        自營品牌單店常用的指標（參考 Amazon 報表的命名，定義以這裡為準），以及在 tracking plan v{data.tracking_plan_version} 下能不能算、還缺什麼、要找誰。
      </p>
      <p className="status-summary">
        {(Object.keys(STATUS) as Status[]).map((s) => (
          <span key={s} className={`status-chip ${s}`} title={STATUS[s].hint}>
            {STATUS[s].icon} {STATUS[s].label} {data.summary[s]}
          </span>
        ))}
      </p>

      {byOwner.size > 0 && (
        <div className="callout">
          <p><b>要補齊指標庫，需要找這些人</b></p>
          <ul>
            {[...byOwner].map(([owner, items]) => (
              <li key={owner} className="small"><b>{owner}</b>：{[...items].join("；")}</li>
            ))}
          </ul>
        </div>
      )}

      {Object.entries(data.groups).map(([group, label]) => (
        <section key={group}>
          <h2>{label}</h2>
          <ul className="metric-grid">
            {data.metrics.filter((m) => m.group === group).map((m) => (
              <li key={m.name} className={`card library-card ${m.status}`}>
                <div className="metric-card-head">
                  <b>{m.label}</b>
                  <span className={`status-chip ${m.status}`} title={STATUS[m.status].hint}>
                    {STATUS[m.status].icon} {STATUS[m.status].label}
                  </span>
                </div>
                <p className="small">{m.definition}</p>
                <p className="small muted">{m.why}</p>
                <p className="small muted">{m.grain}{m.reference && `　·　${m.reference}`}</p>
                {m.missing.length > 0 && (
                  <ul className="missing">
                    {m.missing.map((miss, i) => (
                      <li key={i} className="small"><span className="tag neutral">{miss.owner}</span> {miss.what}</li>
                    ))}
                  </ul>
                )}
                {m.defined && (
                  <p className="small">
                    <Link to={{ pathname: `/console/metrics/${m.name}`, search: planQuery(plan) }}>看這個指標怎麼算 →</Link>
                  </p>
                )}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </>
  );
}
