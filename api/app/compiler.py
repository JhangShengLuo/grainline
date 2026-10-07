"""把 L2–L4 規格編譯成 DuckDB view。

所有 view 都建在不可變的 raw_events 上，查詢時才計算：改了 YAML 只要重新編譯，不需要 backfill。
所有名稱都已經過 spec 的 snake_case 檢查，所以可以直接放進 SQL；只有值需要跳脫。
血緣斷掉的欄位、model、報表會被跳過（R3 會報告原因）；strict=True 時改為直接報錯。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import duckdb

from .lineage import DUCK_TYPES, Lineage, resolve, source_view
from .spec import Column, Filter, Project, Property, RatioMetric, RollupMetric, SimpleMetric

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
GRAINS = ("day", "week", "month")


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


def _model_view(project: Project, lineage: Lineage, name: str) -> View | None:
    if name in lineage.broken_models:
        return None
    model = project.models[name]
    usable = lineage.usable_columns(name)
    select = ",\n    ".join(f"{_column_expr(model.columns[col])} as {col}" for col in usable)
    sql = f"select\n    {select}\nfrom {source_view(model)}"
    if model.kind == "entity":
        sql += "\ngroup by " + ", ".join(model.columns[g].from_ for g in model.grain)
    return View(name=name, layer="L3", sql=sql, columns=tuple(usable))


# ---------- L4 ----------


def _filter_sql(f: Filter) -> str:
    if isinstance(f.value, list):
        values = ", ".join(literal(v) for v in f.value)
        return f"{f.column} {'in' if f.op == 'in' else 'not in'} ({values})"
    ops = {"eq": "=", "neq": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
    return f"{f.column} {ops[f.op]} {literal(f.value)}"


def where_sql(metric: SimpleMetric) -> str:
    if not metric.filters:
        return ""
    return "\nwhere " + " and ".join(_filter_sql(f) for f in metric.filters)


def _measure_sql(metric: SimpleMetric) -> str:
    m = metric.measure
    match m.agg:
        case "count":
            return f"count({m.column})" if m.column else "count(*)"
        case "count_distinct":
            return f"count(distinct {m.column})"
    return f"{m.agg}({m.column})"


def zero_when_empty(project: Project, name: str) -> bool:
    metric = project.metric_layer.metrics[name]
    if isinstance(metric, SimpleMetric):
        return metric.measure.agg in _ZERO_WHEN_EMPTY
    if isinstance(metric, RollupMetric):
        return metric.agg == "sum" and zero_when_empty(project, metric.of)
    return False


def _value(project: Project, alias: str, name: str) -> str:
    return f"coalesce({alias}.{name}, 0)" if zero_when_empty(project, name) else f"{alias}.{name}"


def metric_sql(project: Project, name: str, grain: str, dimensions: Sequence[str] = ()) -> str:
    """任一指標在某個時間 grain（day / week / month / all）與維度下的 SQL，欄位為 period、各維度、指標名稱。

    呼叫前要先確認血緣沒斷（lineage.broken_metrics）。
    """
    metric = project.metric_layer.metrics[name]
    dims = "".join(f", {d}" for d in dimensions)

    def bucket(column: str) -> str:
        # grain "all"：整段期間算成一個值（版本比較用）
        return "cast(null as date)" if grain == "all" else f"cast(date_trunc('{grain}', {column}) as date)"

    if isinstance(metric, SimpleMetric):
        return (
            f"select {bucket(metric.time_column)} as period{dims},\n"
            f"    {_measure_sql(metric)} as {name}\n"
            f"from {metric.model}{where_sql(metric)}\n"
            "group by all"
        )

    if isinstance(metric, RollupMetric):
        inner = metric_sql(project, metric.of, metric.from_grain, dimensions)
        return (
            f"select {bucket('period')} as period{dims},\n"
            f"    {metric.agg}({metric.of}) as {name}\n"
            f"from (\n{inner}\n)\n"
            "group by all"
        )

    # 比率：先在同一個 grain 上分別算出分子、分母，再相除；不會對比率本身做加總
    assert isinstance(metric, RatioMetric)
    keys = ["period", *dimensions]
    num, den = metric.numerator, metric.denominator
    select_keys = ", ".join(f"coalesce(n.{k}, d.{k}) as {k}" for k in keys)
    on = " and ".join(f"n.{k} is not distinct from d.{k}" for k in keys)
    return (
        f"select {select_keys},\n"
        f"    cast({_value(project, 'n', num)} as double) / nullif({_value(project, 'd', den)}, 0) as {name}\n"
        f"from (\n{metric_sql(project, num, grain, dimensions)}\n) n\n"
        f"full join (\n{metric_sql(project, den, grain, dimensions)}\n) d on {on}"
    )


def _report_view(project: Project, lineage: Lineage, name: str) -> View | None:
    if name in lineage.broken_reports:
        return None
    report = project.metric_layer.reports[name]
    dims = report.dimensions
    keys = ["period", *dims]

    ctes = [
        f"m_{m} as (\n{metric_sql(project, m, report.time_grain, dims)}\n)" for m in report.metrics
    ]
    union = "\nunion\n".join(f"select {', '.join(keys)} from m_{m}" for m in report.metrics)
    ctes.append(f"keys as (\n{union}\n)")
    joins = "".join(
        f"\nleft join m_{m} on " + " and ".join(f"m_{m}.{k} is not distinct from k.{k}" for k in keys)
        for m in report.metrics
    )
    select = [f"k.{k}" for k in keys] + [f"{_value(project, f'm_{m}', m)} as {m}" for m in report.metrics]
    sql = (
        "with " + ",\n".join(ctes)
        + "\nselect " + ", ".join(select)
        + "\nfrom keys k" + joins
        + "\norder by " + ", ".join(f"k.{k}" for k in keys)
    )
    return View(name=f"rpt_{name}", layer="L4", sql=sql, columns=(*keys, *report.metrics))


# ---------- 入口 ----------


def compile_project(project: Project, lineage: Lineage | None = None, strict: bool = False) -> list[View]:
    """依相依順序回傳所有可建立的 view。strict=True 時，血緣有任何斷鏈就一次列出全部錯誤。"""
    lineage = lineage or resolve(project)
    if strict:
        errors = [p.message for p in lineage.problems if p.breaking]
        if errors:
            raise CompileError(errors)
    views = _staging_views(project)
    for name in project.models:
        if view := _model_view(project, lineage, name):
            views.append(view)
    for name in project.metric_layer.reports:
        if view := _report_view(project, lineage, name):
            views.append(view)
    return views


def apply_views(con: duckdb.DuckDBPyConnection, views: Sequence[View]) -> None:
    for view in views:
        con.execute(f"create or replace view {view.name} as\n{view.sql}")
