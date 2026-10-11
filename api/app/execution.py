"""模擬執行：如果照建議清單換首頁精選，接下來會怎樣？

同一個商品世界（同一份真相）、同樣的隨機情境，各跑一次「維持現狀」與「換版位」，差異就是換版位的效果；
換幾組不同的隨機情境重複，看結果的範圍。另外跑一次「隨機挑沒被推的商品放上去」，分辨「選得好」和「隨便換都會漲」。
效果只用後端的訂單明細與商品成本計算，不受埋點設計影響。這是模擬世界的結果，不是對真實營收的預測。
"""

from __future__ import annotations

import random
from statistics import mean
from typing import Any

import duckdb

from .compiler import metric_sql
from .decisions import run_decision
from .generator import Scenario, World, catalog, simulate
from .lineage import Lineage
from .spec import Project


def _measure(world: World) -> dict[str, Any]:
    cost = {p["product_id"]: p["cost"] for p in world.tables["products"]}
    per_product: dict[str, dict[str, float]] = {}
    for line in world.tables["order_lines"]:
        stats = per_product.setdefault(line["product_id"], {"units": 0, "revenue": 0.0, "margin": 0.0})
        stats["units"] += line["quantity"]
        stats["revenue"] += line["line_revenue"]
        stats["margin"] += line["line_revenue"] - line["quantity"] * cost[line["product_id"]]
    totals = {k: sum(p[k] for p in per_product.values()) for k in ("units", "revenue", "margin")}
    exposure = {t["product_id"]: t["impressions"] for t in world.truth}
    return {"totals": totals, "per_product": per_product, "exposure": exposure}


def layout_effects(project: Project, layouts: dict[str, tuple[str, ...]], horizon_days: int = 14,
                   replicates: int = 5) -> dict[str, dict[str, Any]]:
    """任意幾種首頁精選配置，和維持現狀相比的毛利、營收、件數變化（同一組隨機情境逐次相減）。"""
    sim = project.simulation
    runs: dict[str, list[dict[str, float]]] = {k: [] for k in ("control", *layouts)}
    for r in range(replicates):
        seed = sim.seed * 1000 + r + 1
        for key, layout in (("control", None), *layouts.items()):
            world = simulate(project, Scenario(behavior_seed=seed, offset_days=sim.days, days=horizon_days, featured=layout))
            runs[key].append(_measure(world)["totals"])
    out = {}
    for key in layouts:
        diffs = {m: [t[m] - c[m] for t, c in zip(runs[key], runs["control"], strict=True)] for m in ("units", "revenue", "margin")}
        out[key] = {m: _range(v) for m, v in diffs.items()} | {"per_run": diffs}
    return out


def featured_performance(project: Project, con: duckdb.DuckDBPyConnection, featured: list[str]) -> dict[str, float]:
    """首頁精選商品過去的每次曝光毛利（用倉儲裡的資料，不是真相）。"""
    def per_product(metric: str) -> dict[str, float]:
        sql = f"select product_id, {metric} from ({metric_sql(project, metric, 'all', ['product_id'])})"
        return {k: float(v or 0) for k, v in con.execute(sql).fetchall()}

    impressions, margin = per_product("product_impressions"), per_product("gross_margin")
    return {p: margin.get(p, 0.0) / max(impressions.get(p, 0.0), 1.0) for p in featured}


def _range(values: list[float]) -> dict[str, float]:
    return {"mean": mean(values), "min": min(values), "max": max(values)}


def _money(value: float) -> str:
    return f"NT$ {value:+,.0f}"


def simulate_execution(
    project: Project,
    lineage: Lineage,
    con: duckdb.DuckDBPyConnection,
    name: str,
    slots: int = 5,
    horizon_days: int = 14,
    replicates: int = 5,
) -> dict[str, Any]:
    result = run_decision(project, lineage, con, name)
    if result["status"] != "ready":
        return {"status": "blocked", "requirements": result["requirements"]}

    rows, featured = catalog(project)
    names = {r["product_id"]: r["name"] for r in rows}
    slots = min(slots, len(featured), len(result["recommended"]))
    # 換掉首頁精選裡表現最差的幾格（過去的每次曝光毛利最低），表現好的保留
    per_impression = featured_performance(project, con, featured)
    displaced = sorted(featured, key=lambda p: (per_impression[p], p))[:slots]
    keep = [p for p in featured if p not in displaced]
    promoted = [c["entity"] for c in result["recommended"] if c["entity"] not in keep][:slots]
    # 對照組：隨機挑同樣數量、目前沒被推的商品（固定種子，每次看到的是同一組）
    pool = sorted(r["product_id"] for r in rows if r["product_id"] not in featured)
    randomly = random.Random(project.simulation.seed).sample(pool, k=min(len(promoted), len(pool)))

    plans = {
        "control": None,
        "playbook": tuple(keep + promoted),
        "random": tuple(keep + randomly),
    }
    sim = project.simulation
    runs: dict[str, list[dict[str, Any]]] = {k: [] for k in plans}
    for r in range(replicates):
        seed = sim.seed * 1000 + r + 1
        for key, layout in plans.items():
            world = simulate(project, Scenario(behavior_seed=seed, offset_days=sim.days, days=horizon_days, featured=layout))
            runs[key].append(_measure(world))

    def effect(key: str) -> dict[str, Any]:
        diffs = {
            metric: [t[metric] - c[metric] for t, c in
                     zip((run["totals"] for run in runs[key]), (run["totals"] for run in runs["control"]), strict=True)]
            for metric in ("units", "revenue", "margin")
        }
        return {
            "per_run": diffs,
            "units": _range(diffs["units"]),
            "revenue": _range(diffs["revenue"]),
            "margin": _range(diffs["margin"]),
            "positive_runs": sum(d > 0 for d in diffs["margin"]),
        }

    def product_effects(ids: list[str], key: str, role: str) -> list[dict[str, Any]]:
        out = []
        for pid in ids:
            def avg(metric: str, k: str) -> float:
                return mean(run["per_product"].get(pid, {}).get(metric, 0) for run in runs[k])
            out.append({
                "product_id": pid,
                "name": names.get(pid, pid),
                "role": role,
                "exposure_before": mean(run["exposure"][pid] for run in runs["control"]),
                "exposure_after": mean(run["exposure"][pid] for run in runs[key]),
                "units_before": avg("units", "control"),
                "units_after": avg("units", key),
                "margin_change": avg("margin", key) - avg("margin", "control"),
            })
        return out

    playbook, rand = effect("playbook"), effect("random")
    # 選得好的部分：同一組隨機情境下，照劇本換 − 隨機換（逐次配對相減）
    skill_runs = [a - b for a, b in zip(playbook["per_run"]["margin"], rand["per_run"]["margin"], strict=True)]
    skill = {"margin": _range(skill_runs), "per_run": skill_runs, "better_runs": sum(d > 0 for d in skill_runs)}
    baseline_margin = mean(run["totals"]["margin"] for run in runs["control"])
    m = playbook["margin"]
    explanation = {
        "assumptions": [
            f"接下來 {horizon_days} 天，使用者的人數、偏好和過去 {sim.days} 天一樣；每個商品被點、被買的真實傾向不變（模擬器知道，報表看不到）。",
            f"只改首頁精選：過去 {sim.days} 天每次曝光毛利最低的 {slots} 格換成建議的商品，其他 {len(keep)} 格保留。分類頁排序、價格、廣告都不變。",
            "庫存照原本的規則每 7 天補貨；商品賣完就無法加入購物車。",
        ],
        "method": [
            "同一個商品世界、同一組隨機情境（同樣的人、同樣的時間來訪），各跑一次「維持現狀」和「換版位」，兩者的差異就是換版位的效果。",
            f"換 {replicates} 組不同的隨機情境各做一次，看效果的範圍，而不是只看一個數字。",
            f"另外跑一次「隨機挑 {len(randomly)} 個沒被推的商品放上去」，分辨「選得好」和「隨便換都會漲」。",
            "效果用後端的訂單明細與商品成本計算（毛利 = 營收 − 件數 × 成本），不受埋點設計影響。",
        ],
        "uncertainty": [
            f"{replicates} 次模擬裡，毛利變化介於 {_money(m['min'])} 到 {_money(m['max'])}，平均 {_money(m['mean'])}"
            f"（約為維持現狀時毛利的 {m['mean'] / baseline_margin:+.1%}）；{playbook['positive_runs']} 次是增加。",
            f"隨機挑選的對照組平均 {_money(rand['margin']['mean'])}：目前的首頁精選不看商品好壞，換掉本來就會改善。",
            f"「選得好」的部分（照劇本換 − 隨機換，同一組情境逐次相減）平均 {_money(skill['margin']['mean'])}，"
            f"範圍 {_money(skill['margin']['min'])} 到 {_money(skill['margin']['max'])}；{skill['better_runs']}/{replicates} 次劇本比較好。",
        ],
        "limitations": [
            "這是模擬世界：商品真實的吸引力與轉換傾向是我們設定的。真實世界只能從資料估計，會有誤差，效果通常比模擬小。",
            "被換下首頁的商品會少賣，上面的毛利已經扣掉這部分；但沒有考慮長期影響（例如被推的商品評價變多、被換下的商品被遺忘）。",
            "沒有考慮價格變動、廣告、季節與競爭對手。要在真實世界確認，建議先對一部分流量做 A/B 測試。",
        ],
    }
    return {
        "status": "ready",
        "decision": name,
        "horizon_days": horizon_days,
        "replicates": replicates,
        "layout": {
            "keep": [{"product_id": p, "name": names.get(p, p)} for p in keep],
            "displaced": [{"product_id": p, "name": names.get(p, p)} for p in displaced],
            "promoted": [{"product_id": p, "name": names.get(p, p)} for p in promoted],
            "random": [{"product_id": p, "name": names.get(p, p)} for p in randomly],
        },
        "control": {"margin": baseline_margin,
                    "revenue": mean(run["totals"]["revenue"] for run in runs["control"]),
                    "units": mean(run["totals"]["units"] for run in runs["control"])},
        "effects": {"playbook": playbook, "random": rand},
        "skill": skill,
        "products": product_effects(promoted, "playbook", "放上首頁") + product_effects(displaced, "playbook", "換下首頁"),
        "explanation": explanation,
    }
