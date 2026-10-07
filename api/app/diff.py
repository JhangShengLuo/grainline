"""兩個 tracking plan 版本的比較：L1 改了什麼、哪些指標受影響、在同一群模擬使用者上數字差多少。

兩個版本的模擬資料來自同一個 seed：同樣的人、同樣的行為，只有埋點設計不同，
所以數字的差異完全來自 tracking plan。
"""

from __future__ import annotations

from typing import Any, Protocol

import duckdb

from .checks import metric_format
from .compiler import metric_sql
from .lineage import Lineage, underlying_simple
from .spec import Project, Property, SimpleMetric, TrackingPlan


class Built(Protocol):
    project: Project
    lineage: Lineage
    con: duckdb.DuckDBPyConnection

    def findings(self) -> list[Any]: ...


def _describe(prop: Property) -> str:
    parts = [prop.type]
    if prop.enum:
        parts.append(f"enum {prop.enum}")
    if prop.from_:
        parts.append(f"from {prop.from_}")
    return "，".join(parts)


def plan_changes(base: TrackingPlan, target: TrackingPlan) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []

    def add(kind: str, event: str, message: str, prop: str | None = None) -> None:
        changes.append({"kind": kind, "event": event, "property": prop, "message": message})

    for name, event in base.events.items():
        if name not in target.events:
            add("event_removed", name, f"移除事件 {name}（原本在 {', '.join(event.fires_on)} 時送出）")
            continue
        new = target.events[name]
        if set(event.fires_on) != set(new.fires_on):
            add(
                "fires_on_changed", name,
                f"{name} 的送出時機：{', '.join(event.fires_on)} → {', '.join(new.fires_on)}"
                f"（{event.trigger} → {new.trigger}）",
            )
        before, after = base.properties_of(name), target.properties_of(name)
        for prop in before.keys() - after.keys():
            add("property_removed", name, f"{name} 移除 property {prop}", prop)
        for prop in after.keys() - before.keys():
            add("property_added", name, f"{name} 新增 property {prop}（{_describe(after[prop])}）", prop)
        for prop in before.keys() & after.keys():
            if _describe(before[prop]) != _describe(after[prop]):
                add("property_changed", name,
                    f"{name}.{prop}：{_describe(before[prop])} → {_describe(after[prop])}", prop)
    for name, event in target.events.items():
        if name not in base.events:
            add("event_added", name, f"新增事件 {name}（在 {', '.join(event.fires_on)} 時送出）")
    return changes


def metric_events(project: Project, name: str) -> set[str]:
    """指標最終依賴的 L1 事件；"*" 代表依賴全部事件（例如 session）。"""
    events = set()
    for simple in underlying_simple(project, name):
        metric = project.metric_layer.metrics[simple]
        assert isinstance(metric, SimpleMetric)
        events.add(project.models[metric.model].source)
    return events


def _total(state: Built, name: str) -> float | None:
    (value,) = state.con.execute(f"select {name} from (\n{metric_sql(state.project, name, 'all')}\n)").fetchone()
    return value


def compare(base: Built, target: Built) -> dict[str, Any]:
    changes = plan_changes(base.project.tracking_plan, target.project.tracking_plan)
    # 事件集合或送出時機改變，會影響依賴「全部事件」的模型（例如 session 切分）
    structural = [c for c in changes if c["kind"] in ("event_added", "event_removed", "fires_on_changed")]

    metrics = []
    for name, metric in target.project.metric_layer.metrics.items():
        deps = metric_events(target.project, name)
        related = [c["message"] for c in changes if c["event"] in deps]
        if "*" in deps:
            related += [c["message"] for c in structural if c["message"] not in related]
        sides = {}
        for label, state in (("base", base), ("target", target)):
            broken = state.lineage.broken_metrics.get(name)
            sides[label] = {"value": None if broken else _total(state, name), "broken": broken}
        b, t = sides["base"]["value"], sides["target"]["value"]
        delta = t - b if b is not None and t is not None else None
        metrics.append({
            "name": name,
            "label": metric.label,
            "type": metric.type,
            "format": metric_format(target.project, name),
            "base": sides["base"],
            "target": sides["target"],
            "delta": delta,
            "pct": delta / b if delta is not None and b else None,
            "changed": bool(delta) or (sides["base"]["broken"] is None) != (sides["target"]["broken"] is None),
            "related_changes": related,
        })

    reports = [
        {
            "name": name,
            "label": report.label,
            "base_broken": base.lineage.broken_reports.get(name),
            "target_broken": target.lineage.broken_reports.get(name),
        }
        for name, report in target.project.metric_layer.reports.items()
    ]

    def keyed(findings: list[Any]) -> dict[tuple[str, str, str], Any]:
        return {(f.rule, f.kind, f.subject): f for f in findings}

    base_findings, target_findings = keyed(base.findings()), keyed(target.findings())
    plan_info = lambda p: {"version": p.version, "name": p.name, "status": p.status}  # noqa: E731
    return {
        "base": plan_info(base.project.tracking_plan),
        "target": plan_info(target.project.tracking_plan),
        "plan_changes": changes,
        "metrics": metrics,
        "reports": reports,
        "findings": {
            "added": [f.to_dict() for k, f in target_findings.items() if k not in base_findings],
            "resolved": [f.to_dict() for k, f in base_findings.items() if k not in target_findings],
        },
    }
