import { useEffect, useMemo, useState } from "react";

import { api, useApi } from "../api";
import type { Journey, Missing, Model, PersonSummary, Plan, Product, Question, QuestionResult, ShopAction, Step } from "../types";
import { AdvisorCard } from "./AdvisorCard";
import { Dashboard } from "./Dashboard";
import { DecisionCard } from "./DecisionCard";
import { QuestionBar } from "./QuestionBar";
import { ShopPage } from "./ShopPage";
import { Timeline } from "./Timeline";

const LIVE = "live-you";
const initial = new URLSearchParams(window.location.search);
const ZOOMS = [0.5, 0.67, 0.8, 1, 1.25];

export function FlowPage() {
  const plans = useApi<Plan[]>("/plans").data ?? [];
  const questions = useApi<Question[]>("/questions").data ?? [];
  const productList = useApi<Product[]>("/products").data;
  const products = useMemo(() => Object.fromEntries((productList ?? []).map((p) => [p.sku, p])), [productList]);

  const [qid, setQid] = useState(initial.get("q") ?? "budget");
  const [plan, setPlan] = useState(Number(initial.get("plan") ?? 1));
  const [model, setModel] = useState<Model | null>((initial.get("attribution") as Model | null) ?? null);
  const [fanout, setFanout] = useState(initial.get("fanout") === "1");
  const [hover, setHover] = useState<string | null>(null);
  const [personId, setPersonId] = useState<string | null>(initial.get("person"));
  const [zoom, setZoom] = useState(Number(initial.get("zoom") ?? 3));
  const [shown, setShown] = useState(Infinity);
  const [playing, setPlaying] = useState(false);
  const [opened, setOpened] = useState<Step | null>(null);
  const [decision, setDecision] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  const question = questions.find((q) => q.id === qid);
  const attribution = question?.attribution ? (model && question.attribution.options.includes(model) ? model : question.attribution.default) : null;
  const result = useApi<QuestionResult>(`/questions/${qid}?plan=${plan}${attribution ? `&attribution=${attribution}` : ""}`, reload);
  const people = useApi<PersonSummary[]>(`/journeys?plan=${plan}`, reload).data ?? [];
  const live = useApi<Journey | null>(`/live?plan=${plan}`, reload).data;
  const pid = personId ?? people[0]?.person_id ?? null;
  const journey = useApi<Journey>(pid && pid !== LIVE ? `/journeys/${pid}?plan=${plan}` : null, reload).data;
  const current = pid === LIVE ? live : journey;

  // 狀態寫進網址：會議裡可以直接分享「這個畫面」
  useEffect(() => {
    const params = new URLSearchParams({ q: qid, plan: String(plan) });
    if (model) params.set("attribution", model);
    if (fanout) params.set("fanout", "1");
    if (personId) params.set("person", personId);
    window.history.replaceState(null, "", `?${params}`);
  }, [qid, plan, model, fanout, personId]);

  // 播放：一步一步讓資料流進來
  useEffect(() => {
    if (!playing || !current) return;
    if (shown >= current.steps.length) {
      setPlaying(false);
      return;
    }
    const timer = setTimeout(() => setShown((n) => n + 1), 700);
    return () => clearTimeout(timer);
  }, [playing, shown, current]);

  // ctrl / ⌘ + 滾輪縮放畫布
  useEffect(() => {
    const onWheel = (e: WheelEvent) => {
      if (!(e.ctrlKey || e.metaKey) || !(e.target as HTMLElement).closest(".canvas")) return;
      e.preventDefault();
      setZoom((z) => Math.max(0, Math.min(ZOOMS.length - 1, z + (e.deltaY > 0 ? -1 : 1))));
    };
    window.addEventListener("wheel", onWheel, { passive: false });
    return () => window.removeEventListener("wheel", onWheel);
  }, []);

  const choosePerson = (id: string) => {
    setPersonId(id);
    setShown(Infinity);
    setOpened(null);
  };
  const play = () => {
    setShown(1);
    setPlaying(true);
  };
  const decide = () => {
    api<string>(`/questions/${qid}/decision?plan=${plan}${attribution ? `&attribution=${attribution}` : ""}`).then(setDecision);
  };
  const showMissing = (m: Missing) => {
    const guest = people.find((p) => p.label === "訪客結帳");
    if (m.event === "purchase" && guest) choosePerson(guest.person_id);
    setTimeout(() => document.querySelector(`[data-missing="${m.param}"]`)?.scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" }), 400);
  };
  const act = async (a: ShopAction) => {
    const next = await api<Journey>(`/live/actions?plan=${plan}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(a),
    });
    setPersonId(LIVE);
    setShown(Infinity);
    setReload((n) => n + 1);
    setOpened(next.steps[next.steps.length - 1] ?? null);
  };
  const resetLive = async () => {
    await api(`/live`, { method: "DELETE" });
    setPersonId(null);
    setOpened(null);
    setReload((n) => n + 1);
  };

  if (!question || !result.data) {
    return <main className="loading">{result.error ?? "載入中…"}</main>;
  }
  const r = result.data;
  const openedIsLive = pid === LIVE || opened === null;
  return (
    <main className="flow">
      <header className="topbar">
        <span className="brand">Grainline</span>
        <span className="muted small">從顧客的一次點擊，到報表上的一個數字</span>
      </header>
      <QuestionBar questions={questions} selected={qid} onSelect={(id) => { setQid(id); setDecision(null); }} />
      <AdvisorCard question={question} plans={plans} plan={plan} onPlan={(v) => { setPlan(v); setReload((n) => n + 1); }}
        attribution={attribution} onAttribution={setModel} fanout={fanout} onFanout={setFanout}
        missing={r.missing} onDecide={decide} onShowMissing={showMissing} />
      {decision && <DecisionCard text={decision} onClose={() => setDecision(null)} />}
      <Dashboard result={r} fanout={fanout} hover={hover} onHover={setHover} />

      <section className="journey" aria-label="顧客旅程">
        <div className="journey-bar">
          <div className="people">
            <button className={`person ${pid === LIVE ? "on" : ""}`} onClick={() => { setPersonId(LIVE); setOpened({ hit_id: 0, ts: "", sid: "", ev: "page_view", url: "", params: [], page: { kind: "campaign" }, session_start: true, campaign: null, tables: [] }); }}>
              你自己逛 {live ? `· ${live.steps.length} 步` : ""}
            </button>
            {people.map((p) => (
              <button key={p.person_id} className={`person ${p.person_id === pid ? "on" : ""}`} onClick={() => choosePerson(p.person_id)}>
                {p.label} <small>{p.sessions} 次造訪 · {p.orders} 張訂單</small>
              </button>
            ))}
          </div>
          <div className="canvas-tools">
            <button onClick={play} disabled={!current}>▶ 播放</button>
            <button onClick={() => setZoom((z) => Math.max(0, z - 1))} aria-label="縮小">−</button>
            <span className="zoom">{Math.round(ZOOMS[zoom] * 100)}%</span>
            <button onClick={() => setZoom((z) => Math.min(ZOOMS.length - 1, z + 1))} aria-label="放大">＋</button>
          </div>
        </div>
        {opened && (
          <div className="opened">
            <ShopPage page={opened.page} products={products} interactive={openedIsLive} campaign={opened.campaign}
              onAction={act} highlight={undefined} />
            <div className="opened-side">
              {openedIsLive ? (
                <>
                  <p>在左邊的頁面上操作，時間軸會多一步：送出的 hit、解析出的列、儀表板的數字都會跟著變。</p>
                  {live && <button onClick={resetLive}>清除我的旅程</button>}
                </>
              ) : (
                <p className="muted">這是模擬顧客當時看到的頁面。想自己操作，選「你自己逛」。</p>
              )}
              <button onClick={() => setOpened(null)}>收起</button>
            </div>
          </div>
        )}
        <div className="canvas" style={{ zoom: ZOOMS[zoom] }}>
          {current ? (
            <Timeline journey={current} products={products} missing={r.missing.filter((m) => m.status === "missing")} model={attribution}
              fanout={fanout} hover={hover} shown={shown} onOpen={setOpened} />
          ) : (
            <p className="muted empty">{pid === LIVE ? "還沒有操作，在上面的頁面選一個進站來源開始。" : "載入中…"}</p>
          )}
        </div>
      </section>
    </main>
  );
}
