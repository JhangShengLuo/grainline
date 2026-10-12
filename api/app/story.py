"""傳統做法 vs Grainline：同一個「找潛力商品」的需求，兩種做法的對照。

傳統做法的時間軸是示意（依常見的專案流程假設），但時間軸上每個錯誤造成的數字，都由模擬器實際算出：
定義不一致差多少、照前端提案（v2）埋點後數字偏多少、最後用「賣最好的」選品選中幾個、少賺多少。
"""

from __future__ import annotations

import time
from statistics import mean
from typing import Any

import duckdb

from .compiler import metric_sql
from .decisions import evaluate, requirements, run_decision
from .diff import compare
from .execution import layout_effects, simulate_execution
from .generator import catalog
from .spec import Project


def best_sellers(project: Project, con: duckdb.DuckDBPyConnection, k: int) -> list[str]:
    """傳統選法：首頁放銷量前 k 名。"""
    sql = (f"select product_id from ({metric_sql(project, 'units_ordered', 'all', ['product_id'])}) "
           f"order by units_ordered desc, product_id limit {k}")
    return [r[0] for r in con.execute(sql).fetchall()]


def _total(con: duckdb.DuckDBPyConnection, project: Project, metric: str) -> float:
    (value,) = con.execute(f"select {metric} from ({metric_sql(project, metric, 'all')})").fetchone()
    return float(value or 0)


def build_story(current: Any, proposal: Any, name: str = "potential_products") -> dict[str, Any]:
    """current / proposal：兩個版本的倉儲（只含模擬資料），例如 v1 現行與 v2 前端提案。"""
    timings: dict[str, float] = {}

    def timed(key: str, fn: Any) -> Any:
        started = time.perf_counter()
        value = fn()
        timings[key] = time.perf_counter() - started
        return value

    project, con = current.project, current.con

    # 1. 同樣叫「轉換率」：會議紀錄（買家 ÷ 訪客）vs BI 規格（訂單數 ÷ 訪客）
    buyers, orders, uv = (_total(con, project, m) for m in ("buyers", "orders", "uv"))
    definitions = {
        "meeting": {"label": "會議紀錄：購買的人 ÷ 來的人", "value": buyers / uv},
        "bi_spec": {"label": "BI 規格：訂單數 ÷ 來的人", "value": orders / uv},
    }

    # 2. 前端照「好實作」的提案埋點（v2）：數字偏多少、哪些報表做不出來
    diff = timed("diff", lambda: compare(current, proposal))
    metrics = {m["name"]: m for m in diff["metrics"]}
    blocked = requirements(proposal.project, proposal.lineage, name)

    # 3. 選品：劇本 vs 傳統（賣最好的）
    evaluation = timed("evaluate", lambda: evaluate(project, current.lineage, con, name, current.world.truth))
    decision = timed("decide", lambda: run_decision(project, current.lineage, con, name))
    execution = timed("simulate", lambda: simulate_execution(project, current.lineage, con, name))

    _, featured = catalog(project)
    best = best_sellers(project, con, len(featured))
    traditional = timed("simulate_traditional", lambda: layout_effects(project, {"best_sellers": tuple(best)}))["best_sellers"]
    names = {p["product_id"]: p["name"] for p in execution["layout"]["promoted"] + execution["layout"]["displaced"] + execution["layout"]["keep"]}
    rows, _ = catalog(project)
    names |= {r["product_id"]: r["name"] for r in rows}
    potential = set(evaluation["potential"])

    def pct(m: dict[str, Any]) -> float | None:
        return m["pct"]

    return {
        "decision": name,
        "note": "傳統做法的週數是示意（依常見的跨部門專案流程假設），不是量測；每一步的數字由模擬器實際算出。",
        "traditional": {
            "weeks": 11,
            "steps": [
                {"week": 1, "who": "行銷", "what": "開會提需求：「想找被埋沒的好商品」。會議紀錄寫「轉換率高、曝光少」。",
                 "doc": "會議紀錄 v1：轉換率 = 購買的人 ÷ 來的人"},
                {"week": 2, "who": "BI", "what": "依會議紀錄寫報表規格，但轉換率寫成「訂單數 ÷ 來的人」。",
                 "doc": "報表規格 v1：轉換率 = 訂單數 ÷ 來的人",
                 "impact": f"同樣叫轉換率，兩種定義差 {definitions['bi_spec']['value'] / definitions['meeting']['value'] - 1:+.0%}"
                           f"（{definitions['meeting']['value']:.1%} vs {definitions['bi_spec']['value']:.1%}），沒有人發現。"},
                {"week": 3, "who": "前端", "what": "排期。為了好實作：加購改成點擊就送、訂單改在成功頁送、商品瀏覽併進 page_view。",
                 "doc": "埋點文件 v2（前端提案）",
                 "impact": f"加購次數灌水 {pct(metrics['cart_adds']):+.0%}；前端回報的訂單數 {pct(metrics['orders_reported']):+.0%}"
                           f"（沒等到成功頁的漏記、重新整理的重複）；商品頁瀏覽數直接消失。"},
                {"week": 5, "who": "DE", "what": "建表。發現訂單編號重複、商品漏斗報表做不出來，回頭開會。",
                 "impact": "需要補埋：" + "；".join(i for items in blocked["missing_by_owner"].values() for i in items)},
                {"week": 6, "who": "全部", "what": "上線，開始收資料。要等 4 週資料夠了才能看。"},
                {"week": 10, "who": "BI", "what": "報表出來，但數字和財務對不上（前端訂單 vs 後端訂單）。商品漏斗做不出來，最後先用「賣最好的」挑商品放首頁。"},
                {"week": 11, "who": "行銷", "what": "把首頁精選換成銷量前幾名。",
                 "impact": f"選中的真正潛力商品：{len(evaluation['baselines']['best_sellers']['hits'])} / {evaluation['k']}"},
            ],
            "picks": [{"product_id": p, "name": names.get(p, p), "potential": p in potential} for p in best],
            "effect": traditional,
            "hits": len([p for p in best if p in potential]),
        },
        "grainline": {
            "steps": [
                {"who": "行銷", "what": "用一句話寫決策（或選現成劇本）：潛力商品。", "seconds": None},
                {"who": "工具", "what": "反推：需要曝光、商品頁瀏覽、銷量、毛利、庫存；前端要在商品卡片與商品頁埋點。",
                 "seconds": timings["decide"]},
                {"who": "工具", "what": f"檢查前端提案（v{proposal.project.tracking_plan.version}）：" +
                 ("劇本跑不了，缺 " + "、".join(i for items in blocked["missing_by_owner"].values() for i in items) if not blocked["ready"] else "可以執行") +
                 f"；加購灌水 {pct(metrics['cart_adds']):+.0%}、前端回報的訂單 {pct(metrics['orders_reported']):+.0%}"
                 f"（後端訂單不受影響）。自動產生補齊的提案。",
                 "seconds": timings["diff"]},
                {"who": "工具", "what": f"在合成資料上跑劇本並用真相驗證：選中 {len(evaluation['playbook']['hits'])} / {evaluation['k']}。",
                 "seconds": timings["evaluate"]},
                {"who": "工具", "what": "模擬執行換首頁精選，給出效果範圍與白話說明。", "seconds": timings["simulate"]},
                {"who": "DE（Jackie）", "what": "覆核合約與劇本，簽核。", "seconds": None},
                {"who": "前端", "what": "照簽好的 tracking plan 埋點；上線第一天報表與劇本就能跑。", "seconds": None},
            ],
            "picks": [{"product_id": c["entity"], "name": c.get("name", c["entity"]), "potential": c["entity"] in potential}
                      for c in decision["recommended"]],
            "effect": {k: execution["effects"]["playbook"][k] for k in ("units", "revenue", "margin")},
            "hits": len(evaluation["playbook"]["hits"]),
            "compute_seconds": sum(timings[k] for k in ("decide", "diff", "evaluate", "simulate")),
        },
        "comparison": {
            "margin_gap": execution["effects"]["playbook"]["margin"]["mean"] - traditional["margin"]["mean"],
            "control_margin": execution["control"]["margin"],
            "horizon_days": execution["horizon_days"],
            "replicates": execution["replicates"],
            "definitions": definitions,
        },
    }


def across_markets(project: Project, name: str = "potential_products", seeds: tuple[int, ...] = (1, 2, 3, 4, 5, 6),
                   replicates: int = 3) -> dict[str, Any]:
    """在幾個不同的模擬市場（不同的商品與使用者）重複比較：劇本 vs 傳統，看結論穩不穩。"""
    from .execution import featured_performance
    from .generator import simulate
    from .lineage import resolve
    from .warehouse import build_warehouse

    markets = []
    for seed in seeds:
        market = project.model_copy(update={"simulation": project.simulation.model_copy(update={"seed": seed})})
        world = simulate(market)
        con = duckdb.connect()
        lineage = resolve(market)
        build_warehouse(con, market, world, lineage)
        evaluation = evaluate(market, lineage, con, name, world.truth)
        picks = [c["entity"] for c in run_decision(market, lineage, con, name)["recommended"]]
        _, featured = catalog(market)
        perf = featured_performance(market, con, featured)
        slots = min(len(picks), len(featured))
        displaced = sorted(featured, key=lambda p: (perf[p], p))[:slots]
        keep = [p for p in featured if p not in displaced]
        effects = layout_effects(market, {
            "playbook": tuple(keep + picks[:slots]),
            "traditional": tuple(best_sellers(market, con, len(featured))),
        }, replicates=replicates)
        con.close()
        markets.append({
            "seed": seed,
            "potential": len(evaluation["potential"]),
            "playbook_hits": len(evaluation["playbook"]["hits"]),
            "traditional_hits": len(evaluation["baselines"]["best_sellers"]["hits"]),
            "playbook_margin": effects["playbook"]["margin"]["mean"],
            "traditional_margin": effects["traditional"]["margin"]["mean"],
        })
    wins = sum(m["playbook_margin"] > m["traditional_margin"] for m in markets)
    return {
        "markets": markets,
        "k": project.decisions[name].top_k,
        "wins": wins,
        "replicates": replicates,
        "mean_gap": mean(m["playbook_margin"] - m["traditional_margin"] for m in markets),
        "note": f"每個市場的商品（吸引力、轉換傾向、成本）和使用者都不同；每個市場各模擬 {replicates} 次、14 天。",
    }
