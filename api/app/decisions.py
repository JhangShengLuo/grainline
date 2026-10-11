"""L5 決策：執行劇本、反推需求、產生 tracking plan 提案、用模擬器的真相評估選品準不準。

以終為始：劇本寫「要做什麼決策、用哪些指標、怎麼評分、有哪些防呆」，
引擎往回推出需要的指標、表、事件與資料來源，以及前端要在哪個頁面、哪個行為時刻埋什麼；
缺的部分可以直接產生一份 tracking plan 提案。結果只是建議清單，不會自動動預算或版位。
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb
import yaml

from .checks import effective_additivity
from .compiler import metric_sql
from .diff import plan_changes
from .explain import MOMENT_TEXT
from .generator import catalog
from .library import coverage, event_needs, load_library
from .lineage import Lineage, underlying_simple
from .spec import (
    PLANS_DIR,
    Decision,
    DecisionRule,
    Project,
    ShrunkRate,
    SimpleMetric,
    TrackingPlan,
    read_tracking_plans,
)

# 每個行為時刻在商店的哪裡發生：反推到「前端要在哪個頁面、哪個動作埋」
MOMENT_PAGE = {
    "page_load": "每個頁面",
    "product_impression": "首頁精選、分類頁的商品卡片",
    "product_detail_load": "商品頁",
    "add_to_cart_click": "商品頁的「加入購物車」按鈕",
    "add_to_cart_success": "商品頁（購物車 API 回應之後）",
    "checkout_load": "結帳頁",
    "payment_success": "結帳頁（付款 API 回應之後）",
    "order_complete_page_load": "付款成功頁",
    "login_success": "登入頁",
}
OP_TEXT = {"gt": ">", "gte": "≥", "lt": "<", "lte": "≤"}


# ---------- 反推 ----------


def _trace(project: Project, lineage: Lineage, metric: str) -> dict[str, list[Any]]:
    """已定義且可用的指標：往下追到 L3 model、L1 事件（含行為時刻）與業務資料。"""
    models: list[str] = []

    def add_model(name: str) -> None:
        if name in models:
            return
        models.append(name)
        for column in project.models[name].columns.values():
            if column.lookup:
                add_model(column.lookup.model)

    for simple in underlying_simple(project, metric):
        m = project.metric_layer.metrics[simple]
        assert isinstance(m, SimpleMetric)
        add_model(m.model)
    events, sources = [], []
    for name in models:
        model = project.models[name]
        if model.table:
            source = project.sources[model.table]
            sources.append({"source": model.table, "owner": source.owner})
        elif model.source and model.source != "*" and model.source in project.tracking_plan.events:
            moments = project.tracking_plan.events[model.source].fires_on
            events.append({"event": model.source, "moments": list(moments)})
    return {"models": models, "events": events, "sources": sources}


def requirements(project: Project, lineage: Lineage, name: str) -> dict[str, Any]:
    """劇本 → 指標 → 表 → 事件與業務資料 → 前端行為時刻；缺什麼、找誰。"""
    decision = project.decisions[name]
    library = load_library()
    cov = {c["name"]: c for c in coverage(project, lineage, library)}
    inputs, missing_by_owner = [], {}
    events: dict[str, set[str]] = {}
    sources: dict[str, str] = {}
    models: list[str] = []
    for alias, metric in decision.inputs.items():
        item = cov.get(metric)
        if item is None:
            inputs.append({"alias": alias, "metric": metric, "label": metric, "status": "blocked",
                           "missing": [{"kind": "library", "owner": "BI", "what": f"指標庫沒有 {metric}"}]})
            continue
        problems = []
        if item["defined"] and not item["broken"]:
            if effective_additivity(project, metric) != "additive":
                problems.append(f"{metric} 不可加：劇本要在時間窗口內加總，輸入必須是可加的指標（R1）")
            for simple in underlying_simple(project, metric):
                model = project.metric_layer.metrics[simple].model  # type: ignore[union-attr]
                if decision.entity not in lineage.usable_columns(model):
                    problems.append(f"{model} 沒有決策對象欄位 {decision.entity}")
            trace = _trace(project, lineage, metric)
        else:
            trace = {"models": [], "events": [], "sources": []}
            for need in event_needs(metric, library):
                trace["events"].append({"event": need.event, "moments": [need.moment] if need.moment else []})
        for m in trace["models"]:
            if m not in models:
                models.append(m)
        for e in trace["events"]:
            events.setdefault(e["event"], set()).update(e["moments"])
        for s in trace["sources"]:
            sources[s["source"]] = s["owner"]
        for miss in item["missing"]:
            missing_by_owner.setdefault(miss["owner"], [])
            if miss["what"] not in missing_by_owner[miss["owner"]]:
                missing_by_owner[miss["owner"]].append(miss["what"])
        inputs.append({
            "alias": alias,
            "metric": metric,
            "label": item["label"],
            "status": item["status"] if not problems else "blocked",
            "missing": item["missing"],
            "problems": problems,
            "broken": item["broken"],
        })
    moments = sorted({m for ms in events.values() for m in ms}, key=list(MOMENT_PAGE).index)
    ready = all(i["status"] in ("available", "differs") for i in inputs)
    return {
        "decision": name,
        "label": decision.label,
        "ready": ready,
        "inputs": inputs,
        "missing_by_owner": missing_by_owner,
        "layers": {
            "metrics": [{"name": i["metric"], "label": i["label"], "status": i["status"]} for i in inputs],
            "models": models,
            "events": [{"event": e, "moments": sorted(ms)} for e, ms in events.items()],
            "sources": [{"source": s, "owner": o} for s, o in sources.items()],
            "moments": [{"moment": m, "text": MOMENT_TEXT[m], "where": MOMENT_PAGE[m]} for m in moments],
        },
    }


# ---------- 執行劇本 ----------


def _period(project: Project, decision: Decision, as_of: date | None) -> tuple[date, date]:
    """[start, end)：資料截止到 as_of（不含），往回看 window_days 天。"""
    sim = project.simulation
    end = as_of or (sim.start_date + timedelta(days=sim.days))
    start = sim.start_date if decision.window_days is None else max(sim.start_date, end - timedelta(days=decision.window_days))
    return start, end


def _values(con: duckdb.DuckDBPyConnection, project: Project, decision: Decision,
            start: date, end: date) -> dict[str, dict[str, float]]:
    rows: dict[str, dict[str, float]] = {}
    for alias, metric in decision.inputs.items():
        sql = (
            f"select {decision.entity} as entity, sum({metric}) as value\n"
            f"from (\n{metric_sql(project, metric, 'day', [decision.entity])}\n)\n"
            f"where period >= ? and period < ? and {decision.entity} is not null group by 1"
        )
        for entity, value in con.execute(sql, [start, end]).fetchall():
            rows.setdefault(entity, {})[alias] = float(value or 0)
    for values in rows.values():
        for alias in decision.inputs:
            values.setdefault(alias, 0.0)
    return rows


def _percentile(values: list[float], p: int) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def _threshold(rule: DecisionRule, rows: dict[str, dict[str, float]]) -> float:
    if isinstance(rule.value, str):
        return _percentile([v[rule.input] for v in rows.values()], int(rule.value[1:]))
    return float(rule.value)


def _passes(value: float, op: str, threshold: float) -> bool:
    return {"gt": value > threshold, "gte": value >= threshold, "lt": value < threshold, "lte": value <= threshold}[op]


def _rate(spec: ShrunkRate, values: dict[str, float], overall: float) -> float:
    num, den = values[spec.numerator], values[spec.denominator]
    if spec.prior_strength > 0:
        return (num + spec.prior_strength * overall) / (den + spec.prior_strength)
    return num / den if den else overall


def _labels(con: duckdb.DuckDBPyConnection, project: Project, entity: str) -> dict[str, dict[str, Any]]:
    """決策對象的名稱：找一個一列一個 entity、又有 name 欄位的 model（例如 dim_products）。"""
    for name, model in project.models.items():
        if model.grain == [entity] and "name" in model.columns:
            extra = ", category" if "category" in model.columns else ""
            try:
                return {r[0]: {"name": r[1], **({"category": r[2]} if extra else {})}
                        for r in con.execute(f"select {entity}, name{extra} from {name}").fetchall()}
            except duckdb.Error:
                return {}
    return {}


def run_decision(project: Project, lineage: Lineage, con: duckdb.DuckDBPyConnection, name: str,
                 as_of: date | None = None) -> dict[str, Any]:
    decision = project.decisions[name]
    req = requirements(project, lineage, name)
    base = {"decision": name, "label": decision.label, "question": decision.question, "entity": decision.entity,
            "top_k": decision.top_k, "tracking_plan_version": project.tracking_plan.version, "requirements": req}
    if not req["ready"]:
        return base | {"status": "blocked"}

    start, end = _period(project, decision, as_of)
    rows = _values(con, project, decision, start, end)
    totals = {alias: sum(v[alias] for v in rows.values()) for alias in decision.inputs}

    def overall(spec: ShrunkRate) -> float:
        return totals[spec.numerator] / totals[spec.denominator] if totals[spec.denominator] else 0.0

    score = decision.score
    rate_overall = overall(score.rate)
    multiply_overall = overall(score.multiply) if score.multiply else 1.0
    thresholds = {i: _threshold(rule, rows) for i, rule in enumerate(decision.rules)}
    labels = _labels(con, project, decision.entity)

    candidates = []
    for entity, values in rows.items():
        rate = _rate(score.rate, values, rate_overall)
        multiplier = _rate(score.multiply, values, multiply_overall) if score.multiply else 1.0
        raw = values[score.rate.numerator] / values[score.rate.denominator] if values[score.rate.denominator] else None
        checks = [
            {
                "input": rule.input,
                "op": rule.op,
                "threshold": thresholds[i],
                "threshold_label": rule.value if isinstance(rule.value, str) else None,
                "value": values[rule.input],
                "passed": _passes(values[rule.input], rule.op, thresholds[i]),
                "why": rule.why,
            }
            for i, rule in enumerate(decision.rules)
        ]
        candidates.append({
            "entity": entity,
            **labels.get(entity, {}),
            "values": values,
            "raw_rate": raw,
            "rate": rate,
            "multiplier": multiplier,
            "score": rate * multiplier,
            "passed": all(c["passed"] for c in checks),
            "checks": checks,
        })
    candidates.sort(key=lambda c: (-c["score"], c["entity"]))
    recommended = [c for c in candidates if c["passed"]][: decision.top_k]
    return base | {
        "status": "ready",
        "window": {"start": str(start), "end": str(end - timedelta(days=1))},
        "score_label": score.label,
        "overall": {"rate": rate_overall, "multiplier": multiply_overall, "score": rate_overall * multiply_overall,
                    "totals": totals},
        "rules": [{"input": r.input, "op": r.op, "op_text": OP_TEXT[r.op], "value": r.value,
                   "threshold": thresholds[i], "why": r.why} for i, r in enumerate(decision.rules)],
        "inputs": decision.inputs,
        "recommended": recommended,
        "candidates": candidates,
    }


# ---------- 產生 tracking plan 提案 ----------


def build_proposal(spec_dir: Path, project: Project, lineage: Lineage, name: str) -> dict[str, Any]:
    """依劇本需要的事件，在目前的 tracking plan 上補齊：新增事件、修正送出時機、補 property。"""
    decision = project.decisions[name]
    library = load_library()
    plan = project.tracking_plan
    raw = plan.model_dump(by_alias=True, exclude_none=True)
    reasons: list[str] = []
    for metric in decision.inputs.values():
        if metric not in library.metrics:
            continue
        for need in event_needs(metric, library):
            events = raw["events"]
            props = {
                prop: {"type": spec.type, **({"from": spec.from_} if spec.from_ else {})}
                for prop, spec in need.properties.items()
            }
            if need.event not in events:
                events[need.event] = {
                    "description": f"{library.metrics[metric].label}需要（自動提案）",
                    "trigger": MOMENT_TEXT[need.moment] if need.moment else "",
                    "fires_on": [need.moment],
                    "properties": props,
                }
                reasons.append(f"{library.metrics[metric].label}需要 {need.event} 事件")
                continue
            event = events[need.event]
            if need.moment and need.moment not in event["fires_on"]:
                # 換掉而不是加上：同一個事件在兩個時刻都送，會重複計算
                reasons.append(f"{library.metrics[metric].label}要求 {need.event} 在 {need.moment} 時送出（原本是 {', '.join(event['fires_on'])}）")
                event["fires_on"] = [need.moment]
                event["trigger"] = MOMENT_TEXT[need.moment]
            declared = {**raw.get("common_properties", {}), **event.get("properties", {})}
            for prop, spec in props.items():
                if prop not in declared:
                    event.setdefault("properties", {})[prop] = spec
                    reasons.append(f"{need.event} 補上 {prop}")

    versions = read_tracking_plans(spec_dir)
    version = max(versions) + 1
    raw.update({
        "version": version,
        "name": f"自動提案：{decision.label}",
        "status": "proposal",
        "description": f"由 L5 劇本「{decision.label}」從 v{plan.version} 反推補齊。" + "；".join(dict.fromkeys(reasons)),
    })
    proposal = TrackingPlan.model_validate(raw)
    changes = plan_changes(plan, proposal)
    header = (
        f"# L1 事件契約 v{version} — 自動提案\n"
        f"#\n# 由 L5 劇本「{decision.label}」從 v{plan.version} 反推產生；請前端與 PM 確認後再採用。\n\n"
    )
    text = header + yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, width=120)
    return {
        "base_version": plan.version,
        "version": version,
        "changes": changes,
        "yaml": text,
        "filename": f"{PLANS_DIR}/v{version}.yaml",
        "needed": bool(changes),
    }


def save_proposal(spec_dir: Path, proposal: dict[str, Any]) -> Path:
    path = spec_dir / proposal["filename"]
    if path.exists():
        raise FileExistsError(f"{proposal['filename']} 已經存在")
    path.write_text(proposal["yaml"], encoding="utf-8")
    return path


# ---------- 用真相評估 ----------


def evaluate(project: Project, lineage: Lineage, con: duckdb.DuckDBPyConnection, name: str,
             truth: list[dict[str, Any]]) -> dict[str, Any]:
    """模擬器知道哪些商品是真正的潛力商品：比較劇本與傳統選法的命中率，以及第幾天開始選得準。"""
    decision = project.decisions[name]
    potential = sorted(t["product_id"] for t in truth if t["is_potential"])
    k = decision.top_k
    result = run_decision(project, lineage, con, name)
    base = {"decision": name, "k": k, "potential": potential, "tracking_plan_version": project.tracking_plan.version,
            "note": "「真正的潛力商品」只有模擬器知道：真實的每次商品頁瀏覽預期毛利（轉換傾向 × 每件毛利）在前 25%、但實際曝光在後 50% 的商品。"}
    if result["status"] != "ready":
        return base | {"status": "blocked", "requirements": result["requirements"]}

    def score(ids: list[str]) -> dict[str, Any]:
        hits = [i for i in ids if i in potential]
        return {"picked": ids, "hits": hits, "precision": len(hits) / k, "recall": len(hits) / len(potential) if potential else 0}

    picked = [c["entity"] for c in result["recommended"]]
    _, featured = catalog(project)
    best = [r[0] for r in con.execute(
        f"select {decision.entity}, sum(units_ordered) as u from ({metric_sql(project, 'units_ordered', 'all', [decision.entity])}) "
        f"group by 1 order by u desc, 1 limit {k}"
    ).fetchall()] if "units_ordered" in project.metric_layer.metrics and "units_ordered" not in lineage.broken_metrics else []

    sim = project.simulation
    curve, first_seen = [], {}
    for day in range(7, sim.days + 1):
        as_of = sim.start_date + timedelta(days=day)
        ids = [c["entity"] for c in run_decision(project, lineage, con, name, as_of)["recommended"]]
        hits = [i for i in ids if i in potential]
        curve.append({"day": day, "date": str(as_of - timedelta(days=1)), "precision": len(hits) / k, "hits": len(hits)})
        for i in hits:
            first_seen.setdefault(i, day)
    return base | {
        "status": "ready",
        "playbook": score(picked),
        "baselines": {
            "best_sellers": {"label": "賣最好的", "description": "依銷售件數挑前幾名", **score(best)},
            "featured": {"label": "首頁精選", "description": "目前首頁精選的前幾個商品", **score(featured[:k])},
        },
        "curve": curve,
        "first_detected": first_seen,
    }
