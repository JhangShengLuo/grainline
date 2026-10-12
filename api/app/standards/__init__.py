"""匯出成語意層標準：Apache Ossie（原 Open Semantic Interchange，OSI），以及經 Apache 官方轉換器轉成 Cube。

Grainline 的 YAML 仍是唯一的作者格式；匯出時：
- L3 model → OSI dataset（grain → primary_key，欄位含型別、是否為時間維度）
- lookup → OSI relationship（多對一）
- L4 指標 → OSI metric（ANSI SQL 聚合運算式，篩選條件轉成 CASE WHEN；比率轉成相除）
- 標準沒有、Grainline 獨有的東西放在 custom_extensions（vendor_name: GRAINLINE）：
  可加性、grain 合約、欄位追到 L1 的血緣、L1 埋點合約、R4 業務規則與對帳、L5 決策劇本、指標庫的白話說明
沒辦法用標準表達的指標（例如 rollup、血緣斷掉的）不匯出，列在模型層的延伸欄位並說明原因。
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from ..checks import Finding, effective_additivity
from ..library import load_library
from ..lineage import Lineage
from ..spec import Filter, Project, RatioMetric, RollupMetric, SimpleMetric

OSSIE_VERSION = "0.2.0.dev0"
OSSIE_COMMIT = "e49a0f0ca2336669ba22662853eba63bff3ce8d1"
VENDOR = "GRAINLINE"
DIALECT = "ANSI_SQL"

_DATATYPES = {
    "string": "String",
    "integer": "Integer",
    "number": "Decimal",
    "boolean": "Boolean",
    "timestamp": "DateTime",
    "date": "Date",
}


@cache
def _schema() -> dict[str, Any]:
    return json.loads((Path(__file__).parent / "ossie-schema.json").read_text(encoding="utf-8"))


def validate_osi(document: dict[str, Any]) -> list[str]:
    """用 Apache Ossie 官方的 JSON Schema 驗證；回傳錯誤訊息（空 = 通過）。"""
    validator = jsonschema.Draft202012Validator(_schema())
    return [
        f"{'/'.join(map(str, e.absolute_path)) or '(root)'}：{e.message}"
        for e in sorted(validator.iter_errors(document), key=lambda e: list(e.absolute_path))
    ]


def _ext(data: dict[str, Any]) -> list[dict[str, str]]:
    return [{"vendor_name": VENDOR, "data": json.dumps(data, ensure_ascii=False, default=str)}]


def _expr(sql: str) -> dict[str, Any]:
    return {"dialects": [{"dialect": DIALECT, "expression": sql}]}


def _literal(value: object) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int | float):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def _condition(dataset: str, f: Filter) -> str:
    column = f"{dataset}.{f.column}"
    if isinstance(f.value, list):
        values = ", ".join(_literal(v) for v in f.value)
        return f"{column} {'IN' if f.op == 'in' else 'NOT IN'} ({values})"
    ops = {"eq": "=", "neq": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
    return f"{column} {ops[f.op]} {_literal(f.value)}"


def _aggregate(project: Project, metric: SimpleMetric) -> str:
    """一個 simple metric 的 ANSI SQL 聚合運算式；篩選條件放進 CASE WHEN，所以不同篩選的指標可以並存。"""
    dataset = metric.model
    measure = metric.measure
    target = f"{dataset}.{measure.column}" if measure.column else f"{dataset}.{project.models[dataset].grain[0]}"
    if metric.filters:
        condition = " AND ".join(_condition(dataset, f) for f in metric.filters)
        target = f"CASE WHEN {condition} THEN {target} END"
    match measure.agg:
        case "count":
            return f"COUNT({target})"
        case "count_distinct":
            return f"COUNT(DISTINCT {target})"
    return f"{measure.agg.upper()}({target})"


def _datasets(project: Project, lineage: Lineage) -> list[dict[str, Any]]:
    datasets = []
    for name, model in project.models.items():
        if name in lineage.broken_models:
            continue
        fields = []
        for col in lineage.usable_columns(name):
            info = lineage.columns[name][col]
            spec = model.columns[col]
            field: dict[str, Any] = {"name": col, "expression": _expr(col)}
            if info.type in _DATATYPES:
                field["datatype"] = _DATATYPES[info.type]
            field["dimension"] = {"is_time": info.type in ("timestamp", "date")}
            how = (
                {"lookup": spec.lookup.model_dump()} if spec.lookup
                else {"expr": spec.expr} if spec.expr
                else {"agg": spec.agg, "from": spec.from_} if spec.agg
                else {"from": spec.from_}
            )
            field["custom_extensions"] = _ext({
                "derivation": how,
                "lineage": [hop.__dict__ for hop in info.hops],
                "grain": col in model.grain,
            })
            fields.append(field)
        datasets.append({
            "name": name,
            "source": f"grainline.main.{name}",
            "primary_key": list(model.grain),
            "description": model.description or name,
            "fields": fields,
            "custom_extensions": _ext({
                "kind": model.kind,
                "grain": model.grain,
                "grain_contract": f"一列代表一個 {'、'.join(model.grain)}",
                "source": {"event": model.source} if model.source else {"business_source": model.table},
            }),
        })
    return datasets


def _relationships(project: Project, lineage: Lineage) -> list[dict[str, Any]]:
    out = []
    for name, model in project.models.items():
        for col, spec in model.columns.items():
            if spec.lookup and lineage.columns.get(name, {}).get(col) and not lineage.columns[name][col].broken:
                target = spec.lookup.model
                rel = {
                    "name": f"{name}_to_{target}",
                    "from": name,
                    "to": target,
                    "from_columns": [spec.lookup.by],
                    "to_columns": list(project.models[target].grain),
                }
                if rel not in out:
                    out.append(rel)
    return out


def _metrics(project: Project, lineage: Lineage, findings: list[Finding]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    library = load_library().metrics
    metrics = project.metric_layer.metrics
    exported, skipped = [], []
    for name, metric in metrics.items():
        if name in lineage.broken_metrics:
            skipped.append({"metric": name, "reason": f"血緣斷掉：{lineage.broken_metrics[name]}"})
            continue
        if isinstance(metric, RollupMetric):
            skipped.append({"metric": name, "reason": "rollup（先依時間分桶再彙總）無法用單一聚合運算式表達；而且這類指標通常違反 R1"})
            continue
        if isinstance(metric, SimpleMetric):
            sql = _aggregate(project, metric)
            datatype = "Integer" if metric.measure.agg in ("count", "count_distinct") else "Decimal"
            details: dict[str, Any] = {"time_column": f"{metric.model}.{metric.time_column}",
                                       "filters": [f.model_dump() for f in metric.filters]}
        else:
            assert isinstance(metric, RatioMetric)
            num, den = metrics[metric.numerator], metrics[metric.denominator]
            assert isinstance(num, SimpleMetric) and isinstance(den, SimpleMetric)
            sql = f"CAST({_aggregate(project, num)} AS DOUBLE PRECISION) / NULLIF({_aggregate(project, den)}, 0)"
            datatype = "Float"
            details = {"kind": metric.kind, "numerator": metric.numerator, "denominator": metric.denominator}
        related = [
            {"rule": f.rule, "severity": f.severity, "message": f.message}
            for f in findings if f.subject == f"metric:{name}" or f"metric:{name}" in f.impacted
        ]
        definition = library.get(name)
        exported.append({
            "name": name,
            "expression": _expr(sql),
            "description": metric.description or metric.label,
            "datatype": datatype,
            "ai_context": {"synonyms": [metric.label]},
            "custom_extensions": _ext({
                "label": metric.label,
                "type": metric.type,
                "additivity": effective_additivity(project, name),
                **details,
                **({"library": {"definition": definition.definition, "why": definition.why,
                                "reference": definition.reference}} if definition else {}),
                **({"findings": related} if related else {}),
            }),
        })
    return exported, skipped


def export_osi(project: Project, lineage: Lineage, findings: list[Finding]) -> dict[str, Any]:
    plan = project.tracking_plan
    metrics, skipped = _metrics(project, lineage, findings)
    return {
        "version": OSSIE_VERSION,
        "name": f"grainline_{project.name}",
        "description": f"Grainline 專案 {project.name}（tracking plan v{plan.version} {plan.name}）匯出的語意模型",
        "ai_context": {
            "instructions": "這是電商的語意模型。指標的可加性、grain 合約與資料品質規則在 custom_extensions（GRAINLINE）裡；"
                            "不可加的指標（count distinct、比率）不能跨期間加總。",
        },
        "datasets": _datasets(project, lineage),
        "relationships": _relationships(project, lineage),
        "metrics": metrics,
        "custom_extensions": _ext({
            "exported_from": "Grainline",
            "ossie_commit": OSSIE_COMMIT,
            "tracking_plan": plan.model_dump(by_alias=True, exclude_none=True),
            "business_sources": {
                name: {"owner": s.owner, "grain": s.grain, "rules": [r.model_dump() for r in s.rules]}
                for name, s in project.sources.items()
            },
            "reconciliations": [r.model_dump() for r in project.metric_layer.reconciliations],
            "decisions": {name: d.model_dump(exclude_none=True) for name, d in project.decisions.items()},
            "not_exported": skipped,
        }),
    }


def to_yaml(document: dict[str, Any]) -> str:
    header = (
        f"# Apache Ossie {OSSIE_VERSION} semantic model, exported by Grainline.\n"
        f"# Validated against core-spec/ossie-schema.json at apache/ossie@{OSSIE_COMMIT[:7]}.\n"
    )
    return header + yaml.safe_dump(document, allow_unicode=True, sort_keys=False, width=120)


def _cube_ready(document: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Cube 每個 cube 的維度和量值共用一個名稱空間；和指標同名的欄位改名（只改要轉 Cube 的副本）。"""
    doc = json.loads(json.dumps(document))
    metric_names = {m["name"] for m in doc["metrics"]}
    notes = []
    for dataset in doc["datasets"]:
        for field in dataset.get("fields", []):
            if field["name"] not in metric_names:
                continue
            old, new = field["name"], f"{field['name']}_value"
            field["name"] = new
            field["expression"] = _expr(old)  # 實體欄位名稱不變
            pattern = re.compile(rf"\b{re.escape(dataset['name'])}\.{re.escape(old)}\b")
            for metric in doc["metrics"]:
                for d in metric["expression"]["dialects"]:
                    d["expression"] = pattern.sub(f"{dataset['name']}.{new}", d["expression"])
            for rel in doc.get("relationships", []):
                for side, cols in (("from", "from_columns"), ("to", "to_columns")):
                    if rel[side] == dataset["name"]:
                        rel[cols] = [new if c == old else c for c in rel[cols]]
            if old in dataset.get("primary_key", []):
                dataset["primary_key"] = [new if c == old else c for c in dataset["primary_key"]]
            notes.append(f"{dataset['name']}.{old} 和指標 {old} 同名，Cube 裡的維度改叫 {new}")
    return doc, notes


def export_cube(document: dict[str, Any], base_cube: str) -> tuple[dict[str, str], list[str]]:
    """經 Apache Ossie 官方的 Cube 轉換器轉成 Cube 的 YAML 檔案（Ossie 沒有對應位置的放在 meta.ossie，不會遺失）。

    base_cube：Cube view 的起點（Cube 的 view 要從一個 cube 出發）。
    """
    from ossie_cube import convert_ossie_to_cube

    doc, notes = _cube_ready(document)
    files, issues = convert_ossie_to_cube(to_yaml(doc), base_cube=base_cube)
    return files, notes + [str(issue) for issue in issues]
