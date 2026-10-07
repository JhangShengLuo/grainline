"""給不同角色看的說明：把規格與血緣翻成白話，並拿一個模擬使用者當例子。

- 業務／行銷：指標是哪些行為、怎麼算出來的，有什麼要小心（explain_metric）
- 前端／PM：每個事件什麼時候送、值從哪裡來、下游誰在用、下游需要但還沒埋的（event_catalog）
- DE：session 與身分規則、每個 model 的 grain、欄位血緣、grain 是否成立、SQL（model_catalog）
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import duckdb

from .checks import Finding, effective_additivity, metric_format
from .compiler import View, filter_sql, measure_sql, metric_sql
from .diff import metric_events
from .ingest import IncomingEvent, plan_warnings
from .lineage import Lineage, underlying_simple
from .spec import Filter, Project, RatioMetric, RollupMetric, SimpleMetric, Staging

MOMENT_TEXT = {
    "page_load": "頁面載入完成時",
    "product_detail_load": "商品詳情載入完成時",
    "add_to_cart_click": "按下「加入購物車」的當下",
    "add_to_cart_success": "購物車 API 回傳成功後",
    "checkout_load": "結帳頁載入完成時",
    "payment_success": "付款成功、後端建立訂單時",
    "order_complete_page_load": "付款成功頁載入時",
    "login_success": "登入成功時",
}
# 送出時機本身會造成的偏差，業務需要知道
MOMENT_CAVEAT = {
    "add_to_cart_click": "按下按鈕就記錄：API 失敗（例如庫存不足、逾時）的點擊也會被算進去。",
    "order_complete_page_load": "在付款成功頁記錄：付款後沒等到頁面就離開的訂單會漏掉，重新整理會重複記錄同一張訂單。",
}
CONTEXT_TEXT = {
    "session.platform": "使用者用的平台（web / app）",
    "page.type": "頁面類型",
    "product.id": "商品編號",
    "product.price": "商品當下售價",
    "item.quantity": "加入的數量",
    "cart.total": "購物車總金額",
    "cart.count": "購物車商品件數",
    "order.id": "訂單編號",
    "order.coupon_code": "使用的折扣碼",
    "login.method": "登入方式",
}
OP_TEXT = {"eq": "是", "neq": "不是", "in": "是其中之一", "not_in": "不是", "gt": "大於", "gte": "至少", "lt": "小於", "lte": "最多"}
GRAIN_TEXT = {"day": "每天", "week": "每週", "month": "每月"}
ADDITIVITY_TEXT = {
    "additive": "可以跨期間、跨分組直接相加",
    "semi_additive": "可以跨分組相加，但不能跨期間相加",
    "non_additive": "不能相加",
}


def identity_text(staging: Staging) -> str:
    if staging.identity == "stitch_to_user":
        return (
            "「人」的算法：同一個裝置登入過，登入前的紀錄也算同一個人；同一個會員在不同裝置登入，也算同一個人。"
            "沒登入的人換裝置，會被算成不同的人。"
        )
    return "「人」的算法：一個裝置就是一個人，不看登入帳號。"


def session_text(staging: Staging) -> str:
    return f"「造訪」的算法：同一個裝置超過 {staging.session_timeout_minutes} 分鐘沒有任何被記錄的動作，就算新的一次造訪。"


def _row_noun(description: str) -> str:
    """「每筆訂單一列」→「筆訂單」"""
    noun = description.removeprefix("每").removesuffix("一列").strip()
    return noun or "列"


def _field_text(project: Project, model_name: str, column: str) -> str:
    model = project.models[model_name]
    spec = model.columns.get(column)
    source = spec.from_ if spec and spec.from_ else column
    fixed = {"person_id": "人", "session_id": "造訪", "timestamp": "時間", "event_id": "事件"}
    if source in fixed:
        return fixed[source]
    plan = project.tracking_plan
    props = plan.common_properties if model.source == "*" else (
        plan.properties_of(model.source) if model.source in plan.events else {}
    )
    prop = props.get(source)
    if prop and prop.description:
        return prop.description
    if prop and prop.from_:
        return CONTEXT_TEXT.get(prop.from_, source)
    return column


def _measure_text(project: Project, metric: SimpleMetric) -> str:
    m = metric.measure
    if m.agg == "count" and m.column is None:
        return f"有幾{_row_noun(project.models[metric.model].description)}"
    field = _field_text(project, metric.model, m.column or "")
    return {
        "count": f"有幾筆{field}",
        "count_distinct": f"有幾個不同的{field}" if field != "造訪" else "有幾次不同的造訪",
        "sum": f"{field}加總",
        "avg": f"{field}平均",
        "min": f"{field}最小值",
        "max": f"{field}最大值",
    }[m.agg]


def _filters_text(project: Project, metric: SimpleMetric) -> str:
    parts = []
    for f in metric.filters:
        value = "、".join(map(str, f.value)) if isinstance(f.value, list) else str(f.value)
        parts.append(f"{_field_text(project, metric.model, f.column)}{OP_TEXT[f.op]} {value}")
    return "只算" + "、".join(parts) if parts else ""


def metric_summary(project: Project, name: str) -> str:
    """一句話說明，給指標清單用。"""
    metric = project.metric_layer.metrics[name]
    labels = project.metric_layer.metrics
    if isinstance(metric, RatioMetric):
        return f"{labels[metric.numerator].label} ÷ {labels[metric.denominator].label}"
    if isinstance(metric, RollupMetric):
        verb = {"sum": "加總", "avg": "平均", "min": "取最小", "max": "取最大"}[metric.agg]
        return f"把{GRAIN_TEXT[metric.from_grain]}的{labels[metric.of].label}{verb}"
    source = project.models[metric.model].source
    origin = "所有事件" if source == "*" else f" {source} 事件"
    filters = _filters_text(project, metric)
    return f"{_measure_text(project, metric)}{'（' + filters + '）' if filters else ''}，來自{origin}"


def _simple_steps(project: Project, lineage: Lineage, name: str) -> list[dict[str, Any]]:
    metric = project.metric_layer.metrics[name]
    assert isinstance(metric, SimpleMetric)
    model = project.models[metric.model]
    plan, staging = project.tracking_plan, project.staging
    steps: list[dict[str, Any]] = []

    if model.source == "*":
        steps.append({"layer": "L1", "title": "記錄", "text": "所有被記錄的事件都會用到，不論是哪一種。"})
    elif model.source not in plan.events:
        steps.append({"layer": "L1", "title": "記錄", "broken": True,
                      "text": f"需要 {model.source} 事件，但 tracking plan v{plan.version} 沒有這個事件，所以算不出來。"})
    else:
        event = plan.events[model.source]
        moments = "、".join(MOMENT_TEXT[m] for m in event.fires_on)
        steps.append({"layer": "L1", "title": "記錄",
                      "text": f"{moments}，前端送出 {model.source} 事件。", "detail": event.trigger})

    uses = {metric.measure.column, *(f.column for f in metric.filters)}
    sources = {model.columns[c].from_ for c in uses if c and c in model.columns}
    l2 = []
    if "person_id" in sources:
        l2.append(identity_text(staging))
    if "session_id" in sources or model.source == "*":
        l2.append(session_text(staging))
    if l2:
        steps.append({"layer": "L2", "title": "整理", "text": " ".join(l2)})

    grain = "、".join(model.grain)
    steps.append({"layer": "L3", "title": "建表",
                  "text": f"整理成「{model.description or metric.model}」的表（{metric.model}，一列代表一個 {grain}）。",
                  "broken": metric.model in lineage.broken_models or None})
    filters = _filters_text(project, metric)
    steps.append({"layer": "L4", "title": "計算",
                  "text": f"依期間{'，' + filters if filters else ''}，計算{_measure_text(project, metric)}。"})
    return steps


def _caveats(project: Project, findings: list[Finding], name: str) -> list[dict[str, str]]:
    metrics = project.metric_layer.metrics
    metric = metrics[name]
    caveats: list[dict[str, str]] = []
    additivity = effective_additivity(project, name)
    if isinstance(metric, RatioMetric):
        caveats.append({"level": "info", "text": "比率不能相加或平均：跨期間、跨分組都要用分子、分母重新算。"})
    elif additivity == "non_additive":
        caveats.append({"level": "info",
                        "text": "不能把每天的數字加起來當作一週（或把各平台加起來當總數）：同一個人來好幾天、用好幾個平台，就會被算好幾次。"})
    simples = underlying_simple(project, name)
    if any(isinstance(metrics[s], SimpleMetric) and metrics[s].measure.column == "person_id" for s in simples):
        caveats.append({"level": "info", "text": "沒登入的人換裝置會被算成不同的人，人數可能偏高。"})

    events = metric_events(project, name)
    for event in sorted(e for e in events if e in project.tracking_plan.events):
        for moment in project.tracking_plan.events[event].fires_on:
            if moment in MOMENT_CAVEAT:
                caveats.append({"level": "warning", "text": f"{event}：{MOMENT_CAVEAT[moment]}"})

    related = {f"metric:{n}" for n in [name, *simples]}
    models = {metrics[s].model for s in simples if isinstance(metrics[s], SimpleMetric)}
    # 只算這個指標真的用到的欄位：例如營收用不到 fct_orders.coupon_code，那個欄位斷掉不影響營收
    used_columns = set()
    for s in simples:
        m = metrics[s]
        if isinstance(m, SimpleMetric):
            used_columns |= {f"{m.model}.{c}" for c in (m.time_column, m.measure.column, *(f.column for f in m.filters)) if c}
    for f in findings:
        hit = (
            f.subject in related
            or related & set(f.impacted)
            or f.subject.removeprefix("model:") in models
            or f.subject in used_columns
        )
        if hit:
            text = f.message + (f"（{f.evidence['summary']}）" if f.evidence and f.evidence.get("summary") else "")
            caveats.append({"level": f.severity, "text": text, "rule": f.rule})
    return caveats


# ---------- 例子：一個模擬使用者的一天 ----------


def _rows(con: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    cursor = con.execute(sql, params or [])
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def _example_day(con: duckdb.DuckDBPyConnection) -> Any:
    """模擬期間正中間的那一天（不用 demo 商店的日期）。"""
    days = [r[0] for r in con.execute(
        "select distinct cast(timestamp as date) from raw_events where source = 'sim' order by 1"
    ).fetchall()]
    return days[len(days) // 2] if days else None


def _conditions(metric: SimpleMetric) -> str:
    return "".join(f" and {filter_sql(f)}" for f in metric.filters)


def _contribution(con: duckdb.DuckDBPyConnection, metric: SimpleMetric, person: str, start: Any, days: int) -> float:
    (value,) = con.execute(
        f"select {measure_sql(metric)} from {metric.model} where person_id = ? "
        f"and cast({metric.time_column} as date) between ? and ? + {days - 1}{_conditions(metric)}",
        [person, start, start],
    ).fetchone()
    return value or 0


def _counted_ids(con: duckdb.DuckDBPyConnection, project: Project, metric: SimpleMetric, person: str, start: Any, days: int) -> set[str] | None:
    """這個人在這段期間，哪些事件會被算進這個 simple metric；None 表示全部事件都會用到。"""
    model = project.models[metric.model]
    if model.source == "*":
        return None
    mapped = [
        Filter(column=model.columns[f.column].from_, op=f.op, value=f.value)
        for f in metric.filters
        if f.column in model.columns and model.columns[f.column].from_
    ]
    where = "".join(f" and {filter_sql(f)}" for f in mapped)
    return {r[0] for r in con.execute(
        f"select event_id from stg_{model.source} where person_id = ? "
        f"and cast(timestamp as date) between ? and ? + {days - 1}{where}",
        [person, start, start],
    ).fetchall()}


def _timeline(con: duckdb.DuckDBPyConnection, person: str, start: Any, days: int,
              marks: dict[str, set[str] | None]) -> list[dict[str, Any]]:
    rows = _rows(con, f"""
select event_id, timestamp, event_name, platform, session_id, json(properties) as properties
from stg_events
where person_id = ? and cast(timestamp as date) between ? and ? + {days - 1}
order by timestamp, event_id
limit 60""", [person, start, start])
    out = []
    for r in rows:
        props = {k: v for k, v in json.loads(r["properties"]).items() if k != "platform"}
        out.append({
            "time": r["timestamp"].strftime("%m-%d %H:%M" if days > 1 else "%H:%M"),
            "event_name": r["event_name"],
            "platform": r["platform"],
            "session": r["session_id"].rsplit("-", 1)[-1],
            "properties": dict(list(props.items())[:4]),
            "marks": [label for label, ids in marks.items() if ids is None or r["event_id"] in ids],
        })
    return out


def _candidates(con: duckdb.DuckDBPyConnection, project: Project, metric: SimpleMetric,
                start: Any, days: int, prefer: int) -> list[str]:
    """這段期間在這個指標裡有資料的人。

    model 的 grain 在資料上有重複時，優先挑有重複的人（例子能直接看到問題）；
    其他依資料筆數接近 prefer 排序（例子比較有代表性）。
    """
    grain = ", ".join(project.models[metric.model].grain)
    return [r[0] for r in con.execute(
        f"select person_id, count(*) as n, count(*) > count(distinct ({grain})) as dup from {metric.model} "
        f"where person_id is not null and person_id not like 'demo-%' "
        f"and cast({metric.time_column} as date) between ? and ? + {days - 1}{_conditions(metric)} "
        f"group by 1 order by dup desc, abs(n - {prefer}), person_id limit 50",
        [start, start],
    ).fetchall()]


def _duplicates(con: duckdb.DuckDBPyConnection, project: Project, metric: SimpleMetric, person: str, start: Any) -> int:
    grain = ", ".join(project.models[metric.model].grain)
    (n,) = con.execute(
        f"select count(*) - count(distinct ({grain})) from {metric.model} "
        f"where person_id = ? and cast({metric.time_column} as date) = ?{_conditions(metric)}",
        [person, start],
    ).fetchone()
    return n


def _period_value(con: duckdb.DuckDBPyConnection, project: Project, name: str, grain: str, period: Any) -> float | None:
    row = con.execute(
        f"select {name} from (\n{metric_sql(project, name, grain)}\n) where period = cast(date_trunc('{grain}', ?::date) as date)",
        [period],
    ).fetchone()
    return row[0] if row else None


def _fmt(value: float | None, fmt: str = "number") -> str:
    if value is None:
        return "—"
    if fmt == "percent":
        return f"{value:.1%}"
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.1f}"


def _example(project: Project, lineage: Lineage, con: duckdb.DuckDBPyConnection, name: str, index: int) -> dict[str, Any] | None:
    metrics = project.metric_layer.metrics
    metric = metrics[name]
    if name in lineage.broken_metrics or (day := _example_day(con)) is None:
        return None

    def has_person(m: SimpleMetric) -> bool:
        return "person_id" in lineage.usable_columns(m.model)

    if isinstance(metric, SimpleMetric):
        if not has_person(metric):
            return None
        prefer = 3 if metric.measure.agg == "count_distinct" else 2
        people = _candidates(con, project, metric, day, 1, prefer)
        if not people:
            return None
        person = people[index % len(people)]
        value = _contribution(con, metric, person, day, 1)
        counted = _counted_ids(con, project, metric, person, day, 1)
        events = _timeline(con, person, day, 1, {"算進來": counted})
        n_counted = sum("算進來" in e["marks"] for e in events)
        total = _period_value(con, project, name, "day", day)
        if metric.measure.agg == "count_distinct":
            what = _field_text(project, metric.model, metric.measure.column or "")
            summary = (f"{person} 在 {day} 有 {n_counted} 筆會被用到的事件，但「{metric.label}」數的是不同的{what}，"
                       f"所以他只算 {_fmt(value)}。")
        elif metric.measure.agg == "count":
            summary = f"{person} 在 {day} 被算了 {_fmt(value)} 筆「{metric.label}」。"
        else:
            summary = f"{person} 在 {day} 的「{metric.label}」是 {_fmt(value)}。"
        if dup := _duplicates(con, project, metric, person, day):
            grain = "、".join(project.models[metric.model].grain)
            summary += f" ⚠ 其中 {dup} 筆是同一個 {grain} 被重複記錄，實際上不該算。"
        summary += f" 這一天全站的{metric.label}是 {_fmt(total, metric_format(project, name))}。"
        return {"person_id": person, "period": str(day), "grain": "day", "events": events,
                "summary": summary, "candidates": len(people), "index": index % len(people)}

    if isinstance(metric, RatioMetric):
        num, den = metrics[metric.numerator], metrics[metric.denominator]
        assert isinstance(num, SimpleMetric) and isinstance(den, SimpleMetric)
        if not (has_person(num) and has_person(den)):
            return None
        people = _candidates(con, project, num, day, 1, prefer=1)
        if not people:
            return None
        person = people[index % len(people)]
        n_value, d_value = _contribution(con, num, person, day, 1), _contribution(con, den, person, day, 1)
        events = _timeline(con, person, day, 1, {
            "分子": _counted_ids(con, project, num, person, day, 1),
            "分母": _counted_ids(con, project, den, person, day, 1),
        })
        n_total = _period_value(con, project, metric.numerator, "day", day)
        d_total = _period_value(con, project, metric.denominator, "day", day)
        ratio = _period_value(con, project, name, "day", day)
        summary = (
            f"{person} 在 {day}：分子「{num.label}」算 {_fmt(n_value)}，分母「{den.label}」算 {_fmt(d_value)}。"
            f" 這一天全站分子 {_fmt(n_total)}、分母 {_fmt(d_total)}，{metric.label} = {_fmt(ratio, metric_format(project, name))}。"
            " 比率是用全站的分子除以分母，不是把每個人的比率平均。"
        )
        if d_value == 0:
            summary += " ⚠ 這個人在分子裡，卻不在同一天的分母裡：分子不是分母的子集。"
        return {"person_id": person, "period": str(day), "grain": "day", "events": events,
                "summary": summary, "candidates": len(people), "index": index % len(people)}

    assert isinstance(metric, RollupMetric)
    of = metrics[metric.of]
    if not isinstance(of, SimpleMetric) or not has_person(of) or metric.from_grain != "day":
        return None
    week = con.execute("select cast(date_trunc('week', ?::date) as date)", [day]).fetchone()[0]
    people = [r[0] for r in con.execute(
        f"select person_id from {of.model} where person_id is not null and person_id not like 'demo-%' "
        f"and cast({of.time_column} as date) between ? and ? + 6{_conditions(of)} "
        f"group by 1 having count(distinct cast({of.time_column} as date)) >= 2 "
        f"order by abs(count(distinct cast({of.time_column} as date)) - 3), person_id limit 50",
        [week, week],
    ).fetchall()]
    if not people:
        return None
    person = people[index % len(people)]
    direct = _contribution(con, of, person, week, 7)
    daily = [_contribution(con, of, person, week + timedelta(days=i), 1) for i in range(7)]
    rolled = sum(daily) if metric.agg == "sum" else None
    events = _timeline(con, person, week, 7, {"算進來": _counted_ids(con, project, of, person, week, 7)})
    active_days = sum(1 for v in daily if v)
    summary = (
        f"{person} 在 {week} 這一週有 {active_days} 天被算進每日的「{of.label}」。"
        f"直接以週計算，他算 {_fmt(direct)}；把每天的值{'加起來' if rolled is not None else metric.agg}，"
        f"他被算了 {_fmt(rolled if rolled is not None else max(daily))}。"
    )
    return {"person_id": person, "period": str(week), "grain": "week", "events": events,
            "summary": summary, "candidates": len(people), "index": index % len(people)}


def explain_metric(project: Project, lineage: Lineage, findings: list[Finding],
                   con: duckdb.DuckDBPyConnection, name: str, example_index: int = 0) -> dict[str, Any]:
    metrics = project.metric_layer.metrics
    metric = metrics[name]
    result: dict[str, Any] = {
        "name": name,
        "label": metric.label,
        "description": metric.description,
        "type": metric.type,
        "format": metric_format(project, name),
        "summary": metric_summary(project, name),
        "additivity": effective_additivity(project, name),
        "additivity_text": ADDITIVITY_TEXT[effective_additivity(project, name)],
        "broken": lineage.broken_metrics.get(name),
        "tracking_plan_version": project.tracking_plan.version,
    }
    if isinstance(metric, SimpleMetric):
        result["steps"] = _simple_steps(project, lineage, name)
    elif isinstance(metric, RatioMetric):
        result["parts"] = [
            {"role": "分子", "name": metric.numerator, "label": metrics[metric.numerator].label,
             "steps": _simple_steps(project, lineage, metric.numerator)},
            {"role": "分母", "name": metric.denominator, "label": metrics[metric.denominator].label,
             "steps": _simple_steps(project, lineage, metric.denominator)},
        ]
        result["formula"] = f"在同一個期間，分別算出{metrics[metric.numerator].label}和{metrics[metric.denominator].label}，再相除。"
    else:
        of = metrics[metric.of]
        verb = {"sum": "加總", "avg": "平均", "min": "取最小", "max": "取最大"}[metric.agg]
        result["formula"] = f"先算出{GRAIN_TEXT[metric.from_grain]}的{of.label}，再把報表期間內的每個值{verb}。"
        if isinstance(of, SimpleMetric):
            result["parts"] = [{"role": "先算", "name": metric.of, "label": of.label,
                                "steps": _simple_steps(project, lineage, metric.of)}]
    result["caveats"] = _caveats(project, findings, name)
    result["example"] = _example(project, lineage, con, name, example_index)
    return result


def metric_list(project: Project, lineage: Lineage, findings: list[Finding]) -> list[dict[str, Any]]:
    out = []
    for name, metric in project.metric_layer.metrics.items():
        caveats = _caveats(project, findings, name)
        out.append({
            "name": name,
            "label": metric.label,
            "type": metric.type,
            "summary": metric_summary(project, name),
            "broken": lineage.broken_metrics.get(name),
            "problems": sum(c["level"] in ("error", "warning") for c in caveats),
            "reports": [r for r, rep in project.metric_layer.reports.items() if name in rep.metrics],
        })
    return out


# ---------- 前端／PM：埋點清單 ----------


def event_catalog(project: Project, lineage: Lineage, con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    plan = project.tracking_plan
    metrics = project.metric_layer.metrics
    reports = project.metric_layer.reports
    deps = {name: metric_events(project, name) for name in metrics}

    live = _rows(con, """
select event_id, event_name, timestamp, anonymous_id, user_id, tracking_plan_version, json(properties) as properties
from raw_events where source = 'live'""")
    by_event: dict[str, list[dict[str, Any]]] = {}
    for row in live:
        by_event.setdefault(row["event_name"], []).append(row)

    def live_stats(event: str) -> dict[str, Any]:
        rows = by_event.get(event, [])
        warned = 0
        messages: dict[str, int] = {}
        for r in rows:
            incoming = IncomingEvent.model_validate({**r, "properties": json.loads(r["properties"]),
                                                     "timestamp": r["timestamp"]})
            found = plan_warnings(plan, incoming)
            warned += bool(found)
            for m in found:
                messages[m] = messages.get(m, 0) + 1
        return {
            "received": len(rows),
            "with_warnings": warned,
            "last_seen": max((r["timestamp"] for r in rows), default=None),
            "warnings": [{"message": m, "count": c} for m, c in sorted(messages.items(), key=lambda kv: -kv[1])],
        }

    events = []
    for name, event in plan.events.items():
        models = [m for m, model in project.models.items() if model.source == name]
        used_metrics = [m for m, d in deps.items() if name in d]
        events.append({
            "name": name,
            "description": event.description,
            "trigger": event.trigger,
            "fires_on": [{"moment": m, "text": MOMENT_TEXT[m], "caveat": MOMENT_CAVEAT.get(m)} for m in event.fires_on],
            "properties": [
                {
                    "name": prop_name,
                    "type": prop.type,
                    "required": prop.required,
                    "enum": prop.enum,
                    "min": prop.min,
                    "max": prop.max,
                    "from": prop.from_,
                    "from_text": CONTEXT_TEXT.get(prop.from_ or "", "沒有指定（前端自行填寫）"),
                    "description": prop.description,
                    "common": prop_name in plan.common_properties,
                }
                for prop_name, prop in plan.properties_of(name).items()
            ],
            "used_by": {
                "models": models,
                "metrics": [{"name": m, "label": metrics[m].label} for m in used_metrics],
                "reports": [{"name": r, "label": rep.label} for r, rep in reports.items()
                            if any(m in rep.metrics for m in used_metrics)],
            },
            "live": live_stats(name),
        })

    # 下游需要、但這個版本的 L1 沒有的
    requests = [
        {
            "kind": p.kind,
            "event": p.details.get("event"),
            "property": p.details.get("property"),
            "message": p.message,
            "impacted": p.impacted,
        }
        for p in lineage.problems
        if p.kind in ("missing_event", "missing_property")
    ]
    unknown = [
        {"event_name": name, **live_stats(name)}
        for name in sorted(by_event)
        if name not in plan.events
    ]
    shared_models = [m for m, model in project.models.items() if model.source == "*"]
    return {
        "tracking_plan": {"version": plan.version, "name": plan.name, "status": plan.status,
                          "description": plan.description},
        "shared_models": shared_models,
        "events": events,
        "downstream_requests": requests,
        "unknown_events": unknown,
    }


# ---------- DE：資料模型 ----------


def model_catalog(project: Project, lineage: Lineage, findings: list[Finding], views: list[View],
                  con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    sql = {v.name: v.sql for v in views}
    grain_findings = {f.subject.removeprefix("model:"): f for f in findings if f.kind == "grain_duplicate"}
    metrics = project.metric_layer.metrics
    (unknown,) = con.execute("select count(*) from stg_unknown_events").fetchone()
    staging = project.staging
    models = []
    for name, model in project.models.items():
        broken = lineage.broken_models.get(name)
        rows = None if broken else con.execute(f"select count(*) from {name}").fetchone()[0]
        columns = []
        for col, spec in model.columns.items():
            info = lineage.columns.get(name, {}).get(col)
            columns.append({
                "name": col,
                "from": spec.from_,
                "agg": spec.agg,
                "type": info.type if info else None,
                "broken": info.broken if info else broken,
                "grain": col in model.grain,
                "origin": info.hops[-1].__dict__ if info and info.hops else None,
            })
        grain = grain_findings.get(name)
        models.append({
            "name": name,
            "kind": model.kind,
            "description": model.description,
            "source": model.source,
            "grain": model.grain,
            "rows": rows,
            "broken": broken,
            "columns": columns,
            "grain_check": None if broken else {
                "ok": grain is None,
                "summary": grain.evidence["summary"] if grain and grain.evidence else "grain 在資料上成立：沒有重複。",
            },
            "metrics": [m for m, metric in metrics.items() if isinstance(metric, SimpleMetric) and metric.model == name],
            "sql": sql.get(name),
        })
    return {
        "staging": {
            "session_timeout_minutes": staging.session_timeout_minutes,
            "identity": staging.identity,
            "identity_text": identity_text(staging),
            "session_text": session_text(staging),
            "unknown_events": unknown,
            "sql": {name: sql[name] for name in ("stg_identity", "stg_events") if name in sql},
        },
        "models": models,
    }
