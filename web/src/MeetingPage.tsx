import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { formatPrice } from "./api";
import { useApi } from "./console/api";

interface Range {
  mean: number;
  min: number;
  max: number;
}

interface Story {
  traditional: { weeks: number; hits: number; picks: unknown[]; effect: { margin: Range } };
  grainline: { hits: number; picks: unknown[]; effect: { margin: Range }; compute_seconds: number };
}

interface Decision {
  question: string;
  requirements: {
    layers: {
      metrics: { label: string }[];
      models: string[];
      events: { event: string }[];
      sources: { source: string; owner: string }[];
      moments: { where: string; text: string }[];
    };
  };
}

interface Execution {
  effects: { playbook: { margin: Range } };
  skill: { margin: Range; better_runs: number };
  replicates: number;
  horizon_days: number;
}

interface DiffMetric {
  name: string;
  label: string;
  pct: number | null;
  delta: number | null;
  target: { broken: string | null };
}

interface Finding {
  kind: string;
  severity: string;
  evidence: { violations?: number; summary?: string } | null;
}

const signed = (v: number) => `${v >= 0 ? "+" : "−"}${formatPrice(Math.abs(Math.round(v)))}`;
const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${(v * 100).toFixed(0)}%`);

function Slide({ kicker, title, children, link }: { kicker: string; title: ReactNode; children?: ReactNode; link?: { to: string; label: string } }) {
  return (
    <section className="slide">
      <p className="slide-kicker">{kicker}</p>
      <h1 className="slide-title">{title}</h1>
      <div className="slide-body">{children}</div>
      {link && (
        <Link to={link.to} target="_blank" rel="noreferrer" className="button slide-link">
          {link.label} ↗
        </Link>
      )}
    </section>
  );
}

function Big({ value, label, tone }: { value: ReactNode; label: string; tone?: "good" | "bad" }) {
  return (
    <div className={`big ${tone ?? ""}`}>
      <div className="big-value">{value}</div>
      <div className="big-label">{label}</div>
    </div>
  );
}

export function MeetingPage() {
  const story = useApi<Story>("/story");
  const decision = useApi<Decision>("/decisions/potential_products");
  const execution = useApi<Execution>("/decisions/potential_products/execution");
  const diff = useApi<{ metrics: DiffMetric[] }>("/diff");
  const r4 = useApi<{ findings: Finding[] }>("/checks?rule=R4");
  const [index, setIndex] = useState(0);

  const metrics = new Map(diff.data?.metrics.map((m) => [m.name, m]) ?? []);
  const brokenFunnel = diff.data?.metrics.filter((m) => m.target.broken).map((m) => m.label) ?? [];
  const expired = r4.data?.findings.find((f) => f.kind === "rule_within");
  const overLimit = r4.data?.findings.find((f) => f.kind === "rule_limit");
  const layers = decision.data?.requirements.layers;

  const slides: ReactNode[] = [
    <Slide key="problem" kicker="問題" title={<>報表在埋點之前就開始規劃，<br />四個團隊只能各自猜「到時候會有什麼資料」</>}>
      <p>前端、行銷、BI、DE 分頭開會，只有 DE 知道資料怎麼處理。週 UV ≠ 日 UV 加總、比率的分子分母不是同一群人——上線後才發現。</p>
      {story.data && <Big value={`約 ${story.data.traditional.weeks} 週`} label="一個「找潛力商品」的需求，用開會與文件往返走完（示意）" tone="bad" />}
    </Slide>,
    <Slide key="goal" kicker="以終為始" title={decision.data?.question ?? "從決策開始"}
      link={{ to: "/console/decisions/potential_products", label: "打開劇本" }}>
      {layers && (
        <ol className="chain">
          <li><b>指標</b>{layers.metrics.map((m) => m.label).join("、")}</li>
          <li><b>表</b>{layers.models.join("、")}</li>
          <li><b>資料</b>{[...layers.events.map((e) => `前端 ${e.event}`), ...layers.sources.map((s) => `${s.owner} ${s.source}`)].join("、")}</li>
          <li><b>前端要在哪裡埋</b>{layers.moments.map((m) => m.where).join("、")}</li>
        </ol>
      )}
    </Slide>,
    <Slide key="shop" kicker="看得到的埋點" title="每個數字，背後是哪一個使用者動作"
      link={{ to: "/shop?overlay=1", label: "打開 demo 商店（埋點透視）" }}>
      <p>在商店上直接看：哪裡有埋點、送什麼、會變成哪些數字。點一下「加入購物車」，旁邊就告訴你哪些指標會變。</p>
    </Slide>,
    <Slide key="cost" kicker="前端提案（v2）的代價" title="「比較好埋」的設計，數字會偏多少？"
      link={{ to: "/console/diff", label: "打開版本差異" }}>
      <div className="bigs">
        <Big value={pct(metrics.get("cart_adds")?.pct)} label="加入購物車次數（點擊就送，失敗的也算）" tone="bad" />
        <Big value={pct(metrics.get("orders_reported")?.pct)} label="前端回報的訂單數（沒等到成功頁、重新整理）" tone="bad" />
        <Big value={`${brokenFunnel.length} 個`} label={`指標算不出來：${brokenFunnel.slice(0, 3).join("、")}…`} tone="bad" />
        <Big value={pct(metrics.get("orders")?.pct ?? 0)} label="後端訂單數：一筆都沒變（訂單以後端為準）" tone="good" />
      </div>
    </Slide>,
    <Slide key="picks" kicker="選品" title="選中幾個真正的潛力商品？" link={{ to: "/console/decisions/potential_products", label: "打開驗證" }}>
      {story.data && (
        <div className="bigs">
          <Big value={`${story.data.traditional.hits} / ${story.data.traditional.picks.length}`} label="首頁放銷量前幾名" tone="bad" />
          <Big value={`${story.data.grainline.hits} / ${story.data.grainline.picks.length}`} label="Grainline 劇本" tone="good" />
        </div>
      )}
      <p className="muted">「真正的潛力商品」只有模擬器知道：每次瀏覽的預期毛利前 25%、曝光後 50%。</p>
    </Slide>,
    <Slide key="exec" kicker="模擬執行" title={execution.data ? `照建議換首頁精選，${execution.data.horizon_days} 天毛利` : "照建議換首頁精選"}
      link={{ to: "/console/story", label: "打開 6 個市場的驗證" }}>
      {execution.data && (
        <div className="bigs">
          <Big value={signed(execution.data.effects.playbook.margin.mean)}
            label={`範圍 ${signed(execution.data.effects.playbook.margin.min)} 到 ${signed(execution.data.effects.playbook.margin.max)}（${execution.data.replicates} 次模擬）`} tone="good" />
          <Big value={signed(execution.data.skill.margin.mean)} label={`其中「選得好」的部分（${execution.data.skill.better_runs}/${execution.data.replicates} 次劇本比隨機好）`} tone="good" />
        </div>
      )}
      <p className="muted">這是模擬世界的結果，不是營收預測；真實世界建議先對一部分流量做 A/B 測試。</p>
    </Slide>,
    <Slide key="quality" kicker="資料品質" title="後端資料本身也要有合約" link={{ to: "/console/checks", label: "打開相容性檢查" }}>
      <div className="bigs">
        <Big value={`${expired?.evidence?.violations ?? 0} 筆`} label="過期的折扣碼還是被使用（後端沒檢查有效期間）" tone="bad" />
        <Big value={`${overLimit?.evidence?.violations ?? 0} 筆`} label="超過每人使用上限" tone="bad" />
      </div>
      <p>R4 業務規則寫在資料合約裡：參照完整、有效期間、次數上限、金額一致；前端回報和後端每天對帳。</p>
    </Slide>,
    <Slide key="next" kicker="下一步" title="簽好合約，上線第一天就能跑" link={{ to: "/console/assistant", label: "用一句話寫新的劇本" }}>
      <ol className="chain">
        <li><b>DE 覆核</b>grain 合約、R4 規則、劇本</li>
        <li><b>前端</b>照簽好的 tracking plan 埋點（缺的部分工具已經自動產生提案）</li>
        <li><b>上線當天</b>staging、模型、報表、決策劇本都已經寫好</li>
      </ol>
      {story.data && <Big value={`${story.data.grainline.compute_seconds.toFixed(0)} 秒`} label="工具從決策反推到驗證、模擬執行的計算時間（加上人的覆核）" tone="good" />}
    </Slide>,
  ];

  const go = useCallback((delta: number) => setIndex((i) => Math.max(0, Math.min(slides.length - 1, i + delta))), [slides.length]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (["ArrowRight", "PageDown", " "].includes(e.key)) go(1);
      if (["ArrowLeft", "PageUp"].includes(e.key)) go(-1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [go]);

  return (
    <div className="meeting">
      <div className="meeting-top">
        <Link to="/console/story" className="muted small">← 離開會議模式</Link>
        <span className="muted small">{index + 1} / {slides.length}　← → 切換</span>
      </div>
      {slides[index]}
      <nav className="meeting-nav" aria-label="投影片">
        <button type="button" onClick={() => go(-1)} disabled={index === 0}>上一頁</button>
        <div className="dots">
          {slides.map((_, i) => (
            <button key={i} type="button" aria-label={`第 ${i + 1} 頁`} aria-current={i === index ? "step" : undefined}
              className={i === index ? "dot active" : "dot"} onClick={() => setIndex(i)} />
          ))}
        </div>
        <button type="button" className="primary" onClick={() => go(1)} disabled={index === slides.length - 1}>下一頁</button>
      </nav>
    </div>
  );
}
