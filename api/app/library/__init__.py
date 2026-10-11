"""內建的指標庫，以及「目前的規格能不能算這個指標、還缺什麼」的檢查。"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field, model_validator

from ..lineage import Lineage
from ..spec import ContextField, Ident, Moment, PropertyType, Project, Spec

LIBRARY_DIR = Path(__file__).parent


class PropertyNeed(Spec):
    type: PropertyType
    from_: ContextField | None = Field(None, alias="from")


class Need(Spec):
    event: Ident | None = None
    moment: Moment | None = None
    properties: dict[Ident, PropertyNeed] = {}
    source: Ident | None = None
    columns: list[Ident] = []
    metric: Ident | None = None

    @model_validator(mode="after")
    def _one_kind(self) -> Need:
        if sum(x is not None for x in (self.event, self.source, self.metric)) != 1:
            raise ValueError("needs 的每一項必須是 event、source、metric 其中之一")
        return self


class LibraryMetric(Spec):
    group: str
    label: str
    definition: str
    why: str
    reference: str = ""
    grain: str
    needs: list[Need] = Field(min_length=1)


class Library(Spec):
    groups: dict[str, str]
    metrics: dict[Ident, LibraryMetric]

    @model_validator(mode="after")
    def _references(self) -> Library:
        errors = [f"{name}: group '{m.group}' 不存在" for name, m in self.metrics.items() if m.group not in self.groups]
        errors += [
            f"{name}: 需要的指標 '{n.metric}' 不在指標庫"
            for name, m in self.metrics.items() for n in m.needs if n.metric and n.metric not in self.metrics
        ]
        if errors:
            raise ValueError("\n".join(errors))
        return self


@cache
def load_library(name: str = "marketing") -> Library:
    return Library.model_validate(yaml.safe_load((LIBRARY_DIR / f"{name}.yaml").read_text(encoding="utf-8")))


# available：已可用；differs：算得出來，但做法和指標庫的定義不同；buildable：資料夠、還沒定義；blocked：缺資料
Status = Literal["available", "differs", "buildable", "blocked"]


def _missing_for(project: Project, need: Need) -> list[dict[str, Any]]:
    """這一項需求在目前的 L1 缺什麼；每一項都寫明該找誰。"""
    plan = project.tracking_plan
    if need.event is not None:
        event = plan.events.get(need.event)
        if event is None:
            return [{"kind": "event", "owner": "前端", "event": need.event, "moment": need.moment,
                     "what": f"新增事件 {need.event}（在 {need.moment} 時送出，帶 {', '.join(need.properties) or '無'}）",
                     "need": need.model_dump(by_alias=True, exclude_none=True)}]
        missing = []
        if need.moment and need.moment not in event.fires_on:
            missing.append({"kind": "fires_on", "owner": "前端", "event": need.event, "moment": need.moment,
                            "what": f"{need.event} 要在 {need.moment} 時送出（目前是 {', '.join(event.fires_on)}）"})
        declared = plan.properties_of(need.event)
        for prop, spec in need.properties.items():
            if prop not in declared:
                missing.append({"kind": "property", "owner": "前端", "event": need.event, "property": prop,
                                "what": f"{need.event} 加上 {prop}（{spec.type}）"})
            elif declared[prop].type != spec.type:
                missing.append({"kind": "property", "owner": "前端", "event": need.event, "property": prop,
                                "what": f"{need.event}.{prop} 要是 {spec.type}（目前是 {declared[prop].type}）"})
        return missing
    if need.source is not None:
        source = project.sources.get(need.source)
        if source is None:
            return [{"kind": "source", "owner": "（未指定）", "source": need.source,
                     "what": f"提供業務資料 {need.source}（欄位 {', '.join(need.columns)}）"}]
        return [
            {"kind": "source_column", "owner": source.owner, "source": need.source, "column": col,
             "what": f"業務資料 {need.source} 加上欄位 {col}"}
            for col in need.columns if col not in source.columns
        ]
    return []


def coverage(project: Project, lineage: Lineage, library: Library | None = None) -> list[dict[str, Any]]:
    """指標庫裡每個指標在這個專案的狀態：已可用、資料夠但還沒定義、缺資料（缺什麼、找誰）。"""
    library = library or load_library()
    result: dict[str, dict[str, Any]] = {}

    def resolve(name: str) -> dict[str, Any]:
        if name in result:
            return result[name]
        metric = library.metrics[name]
        missing: list[dict[str, Any]] = []
        for need in metric.needs:
            if need.metric is not None:
                dep = resolve(need.metric)
                missing += [m for m in dep["missing"] if m not in missing]
            else:
                missing += [m for m in _missing_for(project, need) if m not in missing]
        defined = name in project.metric_layer.metrics
        broken = lineage.broken_metrics.get(name) if defined else None
        status: Status
        if defined and not broken:
            status = "differs" if missing else "available"
        else:
            status = "blocked" if missing else "buildable"
        result[name] = {
            "name": name,
            "group": metric.group,
            "group_label": library.groups[metric.group],
            "label": metric.label,
            "definition": metric.definition,
            "why": metric.why,
            "reference": metric.reference,
            "grain": metric.grain,
            "needs": [n.model_dump(exclude_defaults=True, by_alias=True) for n in metric.needs],
            "defined": defined,
            "broken": broken,
            "status": status,
            "missing": missing,
        }
        return result[name]

    for name in library.metrics:
        resolve(name)
    return [result[name] for name in library.metrics]


def event_needs(name: str, library: Library | None = None) -> list[Need]:
    """一個指標（含它依賴的指標）最終需要的前端事件。"""
    library = library or load_library()
    out: list[Need] = []
    for need in library.metrics[name].needs:
        if need.metric is not None:
            out += [n for n in event_needs(need.metric, library) if n not in out]
        elif need.event is not None and need not in out:
            out.append(need)
    return out
