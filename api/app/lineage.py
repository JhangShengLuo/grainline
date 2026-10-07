"""欄位血緣：每個 L3 欄位與 L4 引用能不能一路追回 L1，以及各自的型別。

compiler 只編譯血緣完整的部分；R3 檢查器把斷掉或型別不符的地方變成 finding。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .spec import Filter, Model, Project, RatioMetric, RollupMetric, SimpleMetric

FieldType = Literal["string", "integer", "number", "boolean", "timestamp"]
NUMERIC: set[str] = {"integer", "number"}
DUCK_TYPES = {"string": "VARCHAR", "integer": "BIGINT", "number": "DOUBLE", "boolean": "BOOLEAN"}

ENVELOPE_TYPES: dict[str, FieldType] = {
    "event_id": "string",
    "event_name": "string",
    "timestamp": "timestamp",
    "anonymous_id": "string",
    "user_id": "string",
    "tracking_plan_version": "integer",
}
DERIVED: dict[str, tuple[FieldType, str]] = {
    "person_id": ("string", "L2 身分合併產生"),
    "session_id": ("string", "L2 session 切分產生"),
}
_AGG_TYPES: dict[str, FieldType] = {"count": "integer", "count_distinct": "integer", "sum": "number"}


@dataclass(frozen=True)
class Hop:
    layer: str
    name: str
    detail: str


@dataclass(frozen=True)
class SourceField:
    type: FieldType
    hops: tuple[Hop, ...]
    enum: tuple[str, ...] | None = None


@dataclass(frozen=True)
class ColumnInfo:
    type: FieldType | None
    hops: tuple[Hop, ...]
    enum: tuple[str, ...] | None = None
    broken: str | None = None


@dataclass
class Problem:
    kind: Literal["missing_event", "missing_property", "missing_column", "type_mismatch", "enum_mismatch"]
    subject: str
    message: str
    hops: tuple[Hop, ...]
    breaking: bool = True
    impacted: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def impact(self, owner: str) -> None:
        if owner != self.subject and owner not in self.impacted:
            self.impacted.append(owner)


@dataclass
class Lineage:
    columns: dict[str, dict[str, ColumnInfo]]
    broken_models: dict[str, str]
    broken_metrics: dict[str, str]
    broken_reports: dict[str, str]
    problems: list[Problem]

    def usable_columns(self, model: str) -> list[str]:
        if model in self.broken_models:
            return []
        return [c for c, info in self.columns[model].items() if info.broken is None]


def source_view(model: Model) -> str:
    return "stg_events" if model.source == "*" else f"stg_{model.source}"


def stg_fields(project: Project, source: str) -> dict[str, SourceField]:
    """某個 stg view 的所有欄位，以及每個欄位往下追到 L1 的路徑。"""
    plan = project.tracking_plan
    view = "stg_events" if source == "*" else f"stg_{source}"
    fields: dict[str, SourceField] = {}
    for name, type_ in ENVELOPE_TYPES.items():
        fields[name] = SourceField(
            type_, (Hop("L2", f"{view}.{name}", "直接取用"), Hop("L1", f"信封.{name}", f"{type_}（SDK 自動帶入）"))
        )
    for name, (type_, detail) in DERIVED.items():
        fields[name] = SourceField(type_, (Hop("L2", f"{view}.{name}", detail),))
    props = plan.common_properties if source == "*" else plan.properties_of(source)
    for name, prop in props.items():
        owner = "共用" if name in plan.common_properties else source
        fields[name] = SourceField(
            prop.type,
            (
                Hop("L2", f"{view}.{name}", f"轉型為 {DUCK_TYPES[prop.type]}"),
                Hop("L1", f"{owner}.{name}", prop.type + (f" enum {prop.enum}" if prop.enum else "")),
            ),
            tuple(prop.enum) if prop.enum else None,
        )
    return fields


def underlying_simple(project: Project, name: str) -> list[str]:
    """指標最終依賴的 simple metric（比率展開分子分母、rollup 展開 of）。"""
    metric = project.metric_layer.metrics[name]
    if isinstance(metric, SimpleMetric):
        return [name]
    if isinstance(metric, RollupMetric):
        return underlying_simple(project, metric.of)
    parts = underlying_simple(project, metric.numerator) + underlying_simple(project, metric.denominator)
    return list(dict.fromkeys(parts))


def _filter_desc(f: Filter) -> str:
    return f"{f.column} {f.op} {f.value!r}"


class _Resolver:
    def __init__(self, project: Project) -> None:
        self.project = project
        self.columns: dict[str, dict[str, ColumnInfo]] = {}
        self.broken_models: dict[str, str] = {}
        self.broken_metrics: dict[str, str] = {}
        self.broken_reports: dict[str, str] = {}
        self.problems: list[Problem] = []
        self._column_problem: dict[tuple[str, str], Problem] = {}
        self._model_problems: dict[str, list[Problem]] = {}
        self._metric_problems: dict[str, list[Problem]] = {}

    def run(self) -> Lineage:
        for name, model in self.project.models.items():
            self._model(name, model)
        metrics = self.project.metric_layer.metrics
        # simple → ratio → rollup，確保依賴先處理
        for kind in (SimpleMetric, RatioMetric, RollupMetric):
            for name, metric in metrics.items():
                if isinstance(metric, kind):
                    self._metric(name, metric)
        for name in self.project.metric_layer.reports:
            self._report(name)
        return Lineage(self.columns, self.broken_models, self.broken_metrics, self.broken_reports, self.problems)

    # ---------- L3 ----------

    def _model(self, name: str, model: Model) -> None:
        view = source_view(model)
        plan = self.project.tracking_plan
        if model.source != "*" and model.source not in plan.events:
            hops = (Hop("L3", name, f"source {model.source}"), Hop("L1", model.source, "✗ 事件不存在"))
            problem = Problem(
                "missing_event",
                name,
                f"L3 {name} 的來源事件 {model.source} 不在 L1 v{plan.version}：整個 model 無法建立",
                hops,
                details={"event": model.source},
            )
            self.problems.append(problem)
            self.columns[name] = {}
            self.broken_models[name] = problem.message
            self._model_problems[name] = [problem]
            return
        fields = stg_fields(self.project, model.source)
        event_desc = "L1 共用 property" if model.source == "*" else f"L1 事件 {model.source}"
        columns: dict[str, ColumnInfo] = {}
        for col, c in model.columns.items():
            l3 = Hop("L3", f"{name}.{col}", f"{c.agg}({c.from_ or '*'})" if c.agg else f"from {c.from_}")
            if c.from_ is None:
                columns[col] = ColumnInfo("integer", (l3,))
            elif c.from_ not in fields:
                reason = f"{event_desc} 沒有宣告 property '{c.from_}'"
                hops = (l3, Hop("L2", f"{view}.{c.from_}", "✗ 不存在"))
                columns[col] = ColumnInfo(None, hops, broken=reason)
                problem = Problem(
                    "missing_property",
                    f"{name}.{col}",
                    f"L3 {name}.{col} 引用 '{c.from_}'，但 {reason}：前端沒有埋這個欄位",
                    hops,
                    details={"event": model.source, "property": c.from_},
                )
                self.problems.append(problem)
                self._column_problem[(name, col)] = problem
            else:
                f = fields[c.from_]
                type_ = _AGG_TYPES.get(c.agg, f.type) if c.agg else f.type
                keeps_values = c.agg in (None, "min", "max", "any_value")
                columns[col] = ColumnInfo(type_, (l3, *f.hops), f.enum if keeps_values else None)
        self.columns[name] = columns
        broken_grain = [g for g in model.grain if columns[g].broken]
        if broken_grain:
            self.broken_models[name] = f"grain 欄位 {', '.join(broken_grain)} 血緣斷掉"
            self._model_problems[name] = [self._column_problem[(name, g)] for g in broken_grain]

    # ---------- L4 ----------

    def _ref(self, owner: str, model: str, column: str, role: str) -> tuple[ColumnInfo | None, list[Problem]]:
        """回傳 (欄位資訊, 造成斷鏈的 problems)；欄位可用時 problems 為空。"""
        if model in self.broken_models:
            causes = self._model_problems[model]
            for p in causes:
                p.impact(owner)
            return None, causes
        info = self.columns[model].get(column)
        if info is None:
            subject = f"{owner}.{role}"
            existing = next(
                (p for p in self.problems if p.subject == subject and p.details == {"model": model, "column": column}),
                None,
            )
            if existing:
                return None, [existing]
            hops = (Hop("L4", owner, role), Hop("L3", f"{model}.{column}", "✗ 不存在"))
            problem = Problem(
                "missing_column",
                subject,
                f"{owner} 的 {role} 引用 {model}.{column}，但 L3 {model} 沒有這個欄位",
                hops,
                details={"model": model, "column": column},
            )
            self.problems.append(problem)
            return None, [problem]
        if info.broken:
            problem = self._column_problem[(model, column)]
            problem.impact(owner)
            return None, [problem]
        return info, []

    def _type_problem(self, owner: str, role: str, info: ColumnInfo, message: str, breaking: bool = True,
                      kind: str = "type_mismatch", **details: Any) -> Problem:
        problem = Problem(
            kind,  # type: ignore[arg-type]
            f"{owner}.{role}",
            message,
            (Hop("L4", owner, role), *info.hops),
            breaking=breaking,
            details=details,
        )
        self.problems.append(problem)
        return problem

    def _metric(self, name: str, metric: Any) -> None:
        owner = f"metric:{name}"
        causes: list[Problem] = []
        if isinstance(metric, SimpleMetric):
            causes = self._simple(owner, metric)
        else:
            parts = [metric.of] if isinstance(metric, RollupMetric) else [metric.numerator, metric.denominator]
            for part in parts:
                for p in self._metric_problems.get(part, []):
                    p.impact(owner)
                    causes.append(p)
        causes = [p for p in _unique(causes) if p.breaking]
        if causes:
            self._metric_problems[name] = causes
            self.broken_metrics[name] = causes[0].message

    def _simple(self, owner: str, metric: SimpleMetric) -> list[Problem]:
        causes: list[Problem] = []
        model = metric.model

        info, broken = self._ref(owner, model, metric.time_column, "time_column")
        causes += broken
        if info and info.type != "timestamp":
            causes.append(self._type_problem(
                owner, "time_column", info,
                f"{owner} 的 time_column {model}.{metric.time_column} 是 {info.type}，必須是時間欄位",
            ))

        measure = metric.measure
        if measure.column:
            info, broken = self._ref(owner, model, measure.column, "measure")
            causes += broken
            if info and measure.agg in ("sum", "avg") and info.type not in NUMERIC:
                causes.append(self._type_problem(
                    owner, "measure", info,
                    f"{owner} 對 {model}.{measure.column}（{info.type}）做 {measure.agg}，只能用在數值欄位",
                ))

        for f in metric.filters:
            info, broken = self._ref(owner, model, f.column, "filter")
            causes += broken
            if info:
                self._check_filter(owner, f, info, causes)
        return causes

    def _check_filter(self, owner: str, f: Filter, info: ColumnInfo, causes: list[Problem]) -> None:
        values = f.value if isinstance(f.value, list) else [f.value]
        expected = {"string": str, "boolean": bool, "integer": (int, float), "number": (int, float)}.get(info.type or "")
        wrong = [v for v in values if expected and (not isinstance(v, expected) or (expected is not bool and isinstance(v, bool)))]
        if wrong:
            causes.append(self._type_problem(
                owner, "filter", info,
                f"{owner} 的篩選 {_filter_desc(f)}：欄位是 {info.type}，值 {wrong} 型別不符",
            ))
            return
        if info.enum:
            outside = [v for v in values if v not in info.enum]
            if outside:
                # SQL 跑得動但篩選永遠成立或永遠不成立，所以不算斷鏈
                self._type_problem(
                    owner, "filter", info,
                    f"{owner} 的篩選 {_filter_desc(f)}：{outside} 不在 L1 enum {list(info.enum)} 裡，前端永遠不會送出這個值",
                    breaking=False, kind="enum_mismatch",
                    column=f.column, values=outside, enum=list(info.enum),
                )

    def _report(self, name: str) -> None:
        owner = f"report:{name}"
        report = self.project.metric_layer.reports[name]
        causes: list[Problem] = []
        for metric in report.metrics:
            for p in self._metric_problems.get(metric, []):
                p.impact(owner)
                causes.append(p)
        metrics = self.project.metric_layer.metrics
        for dim in report.dimensions:
            for simple in dict.fromkeys(m for top in report.metrics for m in underlying_simple(self.project, top)):
                model = metrics[simple].model  # type: ignore[union-attr]
                _, broken = self._ref(owner, model, dim, f"dimension {dim}")
                causes += broken
        causes = _unique(causes)
        if causes:
            self.broken_reports[name] = causes[0].message


def _unique(problems: list[Problem]) -> list[Problem]:
    return list({id(p): p for p in problems}.values())


def resolve(project: Project) -> Lineage:
    return _Resolver(project).run()


def metric_lineage(project: Project, lineage: Lineage, name: str) -> dict[str, Any]:
    """一個指標從 L4 到 L1 的完整路徑，給 API / console 顯示。"""
    metric = project.metric_layer.metrics[name]
    base: dict[str, Any] = {
        "metric": name,
        "label": metric.label,
        "type": metric.type,
        "broken": lineage.broken_metrics.get(name),
    }
    if isinstance(metric, RatioMetric):
        return base | {
            "kind": metric.kind,
            "numerator": metric_lineage(project, lineage, metric.numerator),
            "denominator": metric_lineage(project, lineage, metric.denominator),
        }
    if isinstance(metric, RollupMetric):
        return base | {
            "from_grain": metric.from_grain,
            "agg": metric.agg,
            "of": metric_lineage(project, lineage, metric.of),
        }
    refs = [("time_column", metric.time_column)]
    if metric.measure.column:
        refs.append(("measure", metric.measure.column))
    refs += [("filter", f.column) for f in metric.filters]
    fields = []
    for role, column in refs:
        l4 = Hop("L4", f"metric:{name}", role)
        if metric.model in lineage.broken_models:
            hops, broken = (l4, Hop("L3", metric.model, "✗ model 無法建立")), lineage.broken_models[metric.model]
        elif (info := lineage.columns[metric.model].get(column)) is None:
            hops, broken = (l4, Hop("L3", f"{metric.model}.{column}", "✗ 不存在")), "L3 沒有這個欄位"
        else:
            hops, broken = (l4, *info.hops), info.broken
        fields.append({
            "role": role,
            "column": column,
            "type": None if broken else lineage.columns[metric.model][column].type,
            "broken": broken,
            "hops": [hop.__dict__ for hop in hops],
        })
    return base | {
        "model": metric.model,
        "measure": metric.measure.model_dump(),
        "filters": [f.model_dump() for f in metric.filters],
        "fields": fields,
    }
