"""把 L2–L4 規格編譯成 DuckDB view。

所有 view 都建在不可變的 raw_events 上，查詢時才計算：改了 YAML 只要重新編譯，不需要 backfill。
所有名稱都已經過 spec 的 snake_case 檢查，所以可以直接放進 SQL；只有值需要跳脫。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import duckdb

from .spec import Column, Filter, Model, Project, Property, SimpleMetric

DUCK_TYPES = {"string": "VARCHAR", "integer": "BIGINT", "number": "DOUBLE", "boolean": "BOOLEAN"}

STG_BASE_COLUMNS = (
    "event_id",
    "event_name",
    "timestamp",
    "anonymous_id",
    "user_id",
    "person_id",
    "session_id",
    "tracking_plan_version",
)

# 沒有資料的期間要顯示 0 而不是 NULL 的聚合
_ZERO_WHEN_EMPTY = {"count", "count_distinct", "sum"}


@dataclass(frozen=True)
class View:
    name: str
    layer: Literal["L2", "L3", "L4"]
    sql: str
    columns: tuple[str, ...]


class CompileError(ValueError):
    def __init__(self, errors: Sequence[str]) -> None:
        super().__init__("\n".join(errors))
        self.errors = list(errors)


def literal(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def _extract(name: str, prop: Property) -> str:
    return f"try_cast(json_extract_string(properties, '$.{name}') as {DUCK_TYPES[prop.type]}) as {name}"


# ---------- L2 ----------


def _staging_views(project: Project) -> list[View]:
    plan, staging = project.tracking_plan, project.staging
    event_list = ", ".join(literal(e) for e in plan.events)
    common = list(plan.common_properties)

    identity = View(
        name="stg_identity",
        layer="L2",
        sql=(
            "select anonymous_id, max_by(user_id, timestamp) as user_id\n"
            "from raw_events where user_id is not null\n"
            "group by anonymous_id"
        ),
        columns=("anonymous_id", "user_id"),
    )

    if staging.identity == "stitch_to_user":
        # 登入前的匿名事件也歸到同一個人（回溯生效）
        person = "coalesce(r.user_id, i.user_id, r.anonymous_id)"
    else:
        person = "r.anonymous_id"
    common_sql = "".join(f",\n    {_extract(n, p)}" for n, p in plan.common_properties.items())
    events = View(
        name="stg_events",
        layer="L2",
        sql=f"""with base as (
    select r.*, {person} as person_id
    from raw_events r
    left join stg_identity i on i.anonymous_id = r.anonymous_id
    where r.event_name in ({event_list})
),
marked as (
    select *,
        case when lag(timestamp) over w is null
               or timestamp - lag(timestamp) over w > interval '{staging.session_timeout_minutes} minutes'
             then 1 else 0 end as is_new_session
    from base
    window w as (partition by anonymous_id order by timestamp, event_id)
)
select event_id, event_name, timestamp, anonymous_id, user_id, person_id,
    anonymous_id || '-' || sum(is_new_session) over (
        partition by anonymous_id order by timestamp, event_id
        rows between unbounded preceding and current row
    ) as session_id,
    tracking_plan_version{common_sql},
    properties
from marked""",
        columns=(*STG_BASE_COLUMNS, *common, "properties"),
    )

    unknown = View(
        name="stg_unknown_events",
        layer="L2",
        sql=f"select * from raw_events where event_name not in ({event_list})",
        columns=("event_id", "event_name", "timestamp", "anonymous_id", "user_id", "tracking_plan_version", "properties"),
    )

    per_event = []
    for event_name, event in plan.events.items():
        base = ", ".join((*STG_BASE_COLUMNS, *common))
        props = "".join(f",\n    {_extract(n, p)}" for n, p in event.properties.items())
        per_event.append(
            View(
                name=f"stg_{event_name}",
                layer="L2",
                sql=f"select {base}{props}\nfrom stg_events\nwhere event_name = {literal(event_name)}",
                columns=(*STG_BASE_COLUMNS, *common, *event.properties),
            )
        )
    return [identity, events, unknown, *per_event]


# ---------- L3 ----------


def _column_expr(column: Column) -> str:
    match column.agg:
        case None:
            return column.from_
        case "count":
            return f"count({column.from_})" if column.from_ else "count(*)"
        case "count_distinct":
            return f"count(distinct {column.from_})"
    return f"{column.agg}({column.from_})"


def _model_view(name: str, model: Model, views: dict[str, View], errors: list[str]) -> View | None:
    source = "stg_events" if model.source == "*" else f"stg_{model.source}"
    available = set(views[source].columns) - {"properties"}
    missing = [
        f"L3 {name}.{col}: 來源 {source} 沒有欄位 '{c.from_}'（可用：{', '.join(sorted(available))}）"
        for col, c in model.columns.items()
        if c.from_ is not None and c.from_ not in available
    ]
    if missing:
        errors += missing
        return None
    select = ",\n    ".join(f"{_column_expr(c)} as {col}" for col, c in model.columns.items())
    sql = f"select\n    {select}\nfrom {source}"
    if model.kind == "entity":
        sql += "\ngroup by " + ", ".join(model.columns[g].from_ for g in model.grain)
    return View(name=name, layer="L3", sql=sql, columns=tuple(model.columns))


# ---------- L4 ----------


def _filter_sql(f: Filter) -> str:
    if isinstance(f.value, list):
        values = ", ".join(literal(v) for v in f.value)
        return f"{f.column} {'in' if f.op == 'in' else 'not in'} ({values})"
    ops = {"eq": "=", "neq": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
    return f"{f.column} {ops[f.op]} {literal(f.value)}"


def _measure_sql(metric: SimpleMetric) -> str:
    m = metric.measure
    match m.agg:
        case "count":
            return f"count({m.column})" if m.column else "count(*)"
        case "count_distinct":
            return f"count(distinct {m.column})"
    return f"{m.agg}({m.column})"


def metric_query(
    project: Project,
    metric_name: str,
    grain: str,
    dimensions: Sequence[str],
    views: dict[str, View],
) -> tuple[str | None, list[str]]:
    """單一 simple metric 在某個時間 grain 與維度下的 SQL。回傳 (sql, errors)。"""
    metric = project.metric_layer.metrics[metric_name]
    assert isinstance(metric, SimpleMetric)
    view = views.get(metric.model)
    if view is None:
        return None, [f"L4 metric '{metric_name}': L3 model '{metric.model}' 編譯失敗"]
    needed = [metric.time_column, *dimensions, *(f.column for f in metric.filters)]
    if metric.measure.column:
        needed.append(metric.measure.column)
    errors = [
        f"L4 metric '{metric_name}': model {metric.model} 沒有欄位 '{c}'（可用：{', '.join(view.columns)}）"
        for c in dict.fromkeys(needed)
        if c not in view.columns
    ]
    if errors:
        return None, errors
    dims = "".join(f", {d}" for d in dimensions)
    sql = (
        f"select cast(date_trunc('{grain}', {metric.time_column}) as date) as period{dims},\n"
        f"    {_measure_sql(metric)} as {metric_name}\n"
        f"from {metric.model}"
    )
    if metric.filters:
        sql += "\nwhere " + " and ".join(_filter_sql(f) for f in metric.filters)
    return sql + "\ngroup by all", []


def _report_view(project: Project, name: str, views: dict[str, View], errors: list[str]) -> View | None:
    report = project.metric_layer.reports[name]
    metrics = project.metric_layer.metrics
    dims = report.dimensions

    # 比率展開成分子、分母；比率在報表 grain 上由分子分母的彙總值相除，不會對比率本身做加總
    simple: list[str] = []
    for m in report.metrics:
        metric = metrics[m]
        parts = [m] if isinstance(metric, SimpleMetric) else [metric.numerator, metric.denominator]
        simple += [p for p in parts if p not in simple]

    ctes, report_errors = [], []
    for m in simple:
        sql, errs = metric_query(project, m, report.time_grain, dims, views)
        report_errors += errs
        if sql:
            ctes.append(f"m_{m} as (\n{sql}\n)")
    if report_errors:
        errors += [f"L4 report '{name}': {e}" for e in report_errors]
        return None

    key_cols = ", ".join(["period", *dims])
    keys = "\nunion\n".join(f"select {key_cols} from m_{m}" for m in simple)
    ctes.append(f"keys as (\n{keys}\n)")

    def value(m: str) -> str:
        metric = metrics[m]
        assert isinstance(metric, SimpleMetric)
        if metric.measure.agg in _ZERO_WHEN_EMPTY:
            return f"coalesce(m_{m}.{m}, 0)"
        return f"m_{m}.{m}"

    select = [f"k.{c}" for c in ["period", *dims]]
    for m in report.metrics:
        metric = metrics[m]
        if isinstance(metric, SimpleMetric):
            select.append(f"{value(m)} as {m}")
        else:
            num, den = value(metric.numerator), value(metric.denominator)
            select.append(f"cast({num} as double) / nullif({den}, 0) as {m}")

    joins = "".join(
        f"\nleft join m_{m} on m_{m}.period = k.period"
        + "".join(f" and m_{m}.{d} is not distinct from k.{d}" for d in dims)
        for m in simple
    )
    sql = (
        "with " + ",\n".join(ctes)
        + "\nselect " + ", ".join(select)
        + "\nfrom keys k" + joins
        + "\norder by " + ", ".join(f"k.{c}" for c in ["period", *dims])
    )
    return View(name=f"rpt_{name}", layer="L4", sql=sql, columns=("period", *dims, *report.metrics))


# ---------- 入口 ----------


def compile_project(project: Project) -> list[View]:
    """依相依順序回傳所有 view；任何一層有錯就一次列出全部錯誤。"""
    errors: list[str] = []
    views = {v.name: v for v in _staging_views(project)}
    for name, model in project.models.items():
        if view := _model_view(name, model, views, errors):
            views[name] = view
    for name in project.metric_layer.reports:
        if view := _report_view(project, name, views, errors):
            views[view.name] = view
    if errors:
        raise CompileError(errors)
    return list(views.values())


def apply_views(con: duckdb.DuckDBPyConnection, views: Sequence[View]) -> None:
    for view in views:
        con.execute(f"create or replace view {view.name} as\n{view.sql}")
