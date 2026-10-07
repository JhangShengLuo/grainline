"""三條相容性規則：靜態檢查規格，有倉儲連線時再附上假資料上的數字證據。

R1 可加性 / grain：不可加的指標不能從細 grain 加總到粗 grain
R2 比率母體：rate 的分子必須是分母的子集（同一個計數單位、分子篩選包含分母篩選）
R3 血緣可達：L4 用到的每個欄位都要能追回 L1 宣告過的 property，且型別相符
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import duckdb

from .compiler import literal, metric_sql, where_sql
from .lineage import Lineage, Problem, resolve
from .spec import Filter, Project, RatioMetric, RollupMetric, SimpleMetric

Rule = Literal["R1", "R2", "R3"]
GRAIN_ZH = {"day": "日", "week": "週", "month": "月"}
NON_ADDITIVE_AGGS = {"count_distinct", "avg", "min", "max"}


@dataclass
class Finding:
    rule: Rule
    severity: Literal["error", "warning"]
    kind: str
    subject: str
    message: str
    impacted: list[str] = field(default_factory=list)
    evidence: dict[str, Any] | None = None
    # 產生證據時需要的內部資訊，不輸出
    _context: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("_context")
        return data


def effective_additivity(project: Project, name: str) -> str:
    """實際的可加性：count_distinct 等聚合不論宣告為何都不可加。"""
    metric = project.metric_layer.metrics[name]
    if isinstance(metric, SimpleMetric):
        return "non_additive" if metric.measure.agg in NON_ADDITIVE_AGGS else metric.additivity
    if isinstance(metric, RollupMetric):
        inner = effective_additivity(project, metric.of)
        return "additive" if metric.agg == "sum" and inner == "additive" else "non_additive"
    return "non_additive"


def _nests(fine: str, coarse: str) -> bool:
    """fine 的期間能不能整齊地切進 coarse（日可以切進週和月，週不能切進月）。"""
    return fine == coarse or fine == "day"


def _label(project: Project, name: str) -> str:
    return f"{project.metric_layer.metrics[name].label}（{name}）"


def _report_grain(project: Project, metric: str, default: str) -> str:
    for report in project.metric_layer.reports.values():
        if metric in report.metrics:
            return report.time_grain
    return default


def _reports_using(project: Project, metric: str) -> list[str]:
    return [f"report:{r}" for r, rep in project.metric_layer.reports.items() if metric in rep.metrics]


# ---------- R1 ----------


def _r1(project: Project) -> list[Finding]:
    findings: list[Finding] = []
    metrics = project.metric_layer.metrics
    for name, metric in metrics.items():
        if isinstance(metric, SimpleMetric) and metric.measure.agg in NON_ADDITIVE_AGGS and metric.additivity != "non_additive":
            findings.append(Finding(
                "R1", "error", "declared_additivity", f"metric:{name}",
                f"{_label(project, name)} 用 {metric.measure.agg} 計算，不能加總，卻宣告為 {metric.additivity}。"
                "下游若相信這個宣告而跨期間加總，同一個實體會被重複計算。",
                _reports_using(project, name),
                _context={"metric": name, "from_grain": "day", "to_grain": "week", "agg": "sum"},
            ))
        if isinstance(metric, RollupMetric):
            inner = effective_additivity(project, metric.of)
            to_grain = _report_grain(project, name, "week" if metric.from_grain == "day" else "month")
            context = {"metric": metric.of, "from_grain": metric.from_grain, "to_grain": to_grain, "agg": metric.agg}
            if metric.agg in ("sum", "avg") and inner == "non_additive":
                verb = "加總" if metric.agg == "sum" else "平均"
                findings.append(Finding(
                    "R1", "error", "rollup_non_additive", f"metric:{name}",
                    f"{_label(project, name)} 把 {_label(project, metric.of)} 的每{GRAIN_ZH[metric.from_grain]}值{verb}"
                    f"成較粗的期間，但 {metric.of} 不可加，結果會失真。應直接在報表的 grain 上計算 {metric.of}。",
                    _reports_using(project, name),
                    _context=context,
                ))
            elif metric.agg == "sum" and inner == "semi_additive":
                findings.append(Finding(
                    "R1", "error", "rollup_non_additive", f"metric:{name}",
                    f"{_label(project, name)} 把半可加的 {_label(project, metric.of)} 跨時間加總；"
                    "半可加指標（例如快照）只能跨維度加總，跨時間要取期末值或平均。",
                    _reports_using(project, name),
                    _context=context,
                ))

    for rname, report in project.metric_layer.reports.items():
        for mname in report.metrics:
            metric = metrics[mname]
            if isinstance(metric, RollupMetric) and not _nests(metric.from_grain, report.time_grain):
                findings.append(Finding(
                    "R1", "error", "grain_nesting", f"report:{rname}",
                    f"報表 {report.label}（{rname}）的 grain 是{GRAIN_ZH[report.time_grain]}，"
                    f"但 {_label(project, mname)} 是從每{GRAIN_ZH[metric.from_grain]}的值彙總而來；"
                    f"{GRAIN_ZH[metric.from_grain]}不能整齊地切進{GRAIN_ZH[report.time_grain]}（例如一週可能跨兩個月）。",
                    _context={"metric": metric.of, "from_grain": metric.from_grain,
                              "to_grain": report.time_grain, "agg": metric.agg},
                ))
    return findings


# ---------- R2 ----------


def _unit(project: Project, metric: SimpleMetric) -> str | None:
    """計數的單位：count_distinct 數的是欄位代表的實體，count 數的是 model 的列。"""
    if metric.measure.agg == "count_distinct":
        return metric.measure.column
    if metric.measure.agg == "count" and metric.measure.column is None:
        return f"{metric.model} 的列（grain: {', '.join(project.models[metric.model].grain)}）"
    return None


def _filter_desc(filters: list[Filter]) -> str:
    return "、".join(f"{f.column} {f.op} {f.value!r}" for f in filters)


def _r2(project: Project) -> list[Finding]:
    findings: list[Finding] = []
    metrics = project.metric_layer.metrics
    for name, metric in metrics.items():
        if not isinstance(metric, RatioMetric):
            continue
        num, den = metrics[metric.numerator], metrics[metric.denominator]
        assert isinstance(num, SimpleMetric) and isinstance(den, SimpleMetric)
        context = {"ratio": name, "grain": _report_grain(project, name, "day")}
        impacted = _reports_using(project, name)
        if metric.kind == "rate":
            num_unit, den_unit = _unit(project, num), _unit(project, den)
            if num_unit is None or den_unit is None:
                findings.append(Finding(
                    "R2", "error", "rate_unit", f"metric:{name}",
                    f"{_label(project, name)} 是 rate，分子分母都必須是計數（count / count_distinct）；"
                    "金額類的比值請改用 kind: per_unit。",
                    impacted, _context=context,
                ))
            elif num_unit != den_unit:
                findings.append(Finding(
                    "R2", "error", "rate_unit", f"metric:{name}",
                    f"{_label(project, name)} 的分子 {_label(project, metric.numerator)} 數的是 {num_unit}，"
                    f"分母 {_label(project, metric.denominator)} 數的是 {den_unit}。單位不同，比率可能大於 1。",
                    impacted, _context=context,
                ))
            missing = [f for f in den.filters if f not in num.filters]
            if missing:
                findings.append(Finding(
                    "R2", "error", "rate_filter", f"metric:{name}",
                    f"{_label(project, name)} 的分母有篩選 {_filter_desc(missing)}，分子沒有："
                    f"分子會包含不在分母母體裡的 {num_unit or '資料'}。",
                    impacted, _context=context,
                ))
        elif num.model != den.model or sorted(map(repr, num.filters)) != sorted(map(repr, den.filters)):
            findings.append(Finding(
                "R2", "error", "per_unit_population", f"metric:{name}",
                f"{_label(project, name)} 是每單位平均，分子（{num.model}，篩選：{_filter_desc(num.filters) or '無'}）"
                f"和分母（{den.model}，篩選：{_filter_desc(den.filters) or '無'}）必須來自同一個 model、同樣的篩選。",
                impacted, _context=context,
            ))
    return findings


# ---------- R3 ----------


def _r3(lineage: Lineage) -> list[Finding]:
    return [
        Finding(
            "R3", "error", p.kind, p.subject, p.message, list(p.impacted),
            _context={"problem": p},
        )
        for p in lineage.problems
    ]


# ---------- 數字證據 ----------


def _rows(con: duckdb.DuckDBPyConnection, sql: str) -> list[dict[str, Any]]:
    cursor = con.execute(sql)
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def rollup_comparison(
    con: duckdb.DuckDBPyConnection, project: Project, metric: str, from_grain: str, to_grain: str, agg: str = "sum"
) -> dict[str, Any]:
    """同一個指標：直接在 to_grain 計算 vs 先在 from_grain 算再彙總。"""
    fine = metric_sql(project, metric, from_grain)
    direct = metric_sql(project, metric, to_grain)
    rows = _rows(con, f"""
with fine as (
{fine}
),
rolled as (
    select cast(date_trunc('{to_grain}', period) as date) as period, {agg}({metric}) as rolled
    from fine group by 1
),
direct as (
{direct}
)
select period, d.{metric} as direct, r.rolled as rolled, r.rolled - d.{metric} as diff
from direct d full join rolled r using (period)
order by period""")
    total_direct = sum(r["direct"] or 0 for r in rows)
    total_rolled = sum(r["rolled"] or 0 for r in rows)
    diff = total_rolled - total_direct
    pct = diff / total_direct if total_direct else None
    zh_from, zh_to = GRAIN_ZH[from_grain], GRAIN_ZH[to_grain]
    agg_zh = {"sum": "加總", "avg": "平均", "min": "取最小", "max": "取最大"}[agg]
    summary = (
        f"{len(rows)} 個{zh_to}：直接以{zh_to}計算合計 {total_direct:,.4g}，"
        f"每{zh_from}值{agg_zh}後合計 {total_rolled:,.4g}，差 {diff:+,.4g}"
        + (f"（{pct:+.1%}）" if pct is not None else "")
    )
    m = project.metric_layer.metrics[metric]
    if isinstance(m, SimpleMetric) and m.measure.agg == "count_distinct" and agg == "sum":
        summary += f"。差值就是同一個 {m.measure.column} 在同一{zh_to}內出現在多個{zh_from}而被重複計算的次數"
    return {"summary": summary, "metric": metric, "from_grain": from_grain, "to_grain": to_grain,
            "agg": agg, "total_direct": total_direct, "total_rolled": total_rolled, "diff": diff, "rows": rows}


def _period_select(metric: SimpleMetric, grain: str, unit: str) -> str:
    return (
        f"select distinct cast(date_trunc('{grain}', {metric.time_column}) as date) as period, {unit} as unit\n"
        f"from {metric.model}{where_sql(metric)}"
    )


def rate_membership(con: duckdb.DuckDBPyConnection, project: Project, ratio: str, grain: str) -> dict[str, Any]:
    """分子裡有多少實體不在同期分母裡；以及比率超過 1 的期間。"""
    metrics = project.metric_layer.metrics
    metric = metrics[ratio]
    assert isinstance(metric, RatioMetric)
    num, den = metrics[metric.numerator], metrics[metric.denominator]
    assert isinstance(num, SimpleMetric) and isinstance(den, SimpleMetric)
    zh = GRAIN_ZH[grain]

    comparable = (
        num.measure.agg == den.measure.agg == "count_distinct" and num.measure.column == den.measure.column
    )
    if not comparable:
        rows = _rows(con, f"select * from (\n{metric_sql(project, ratio, grain)}\n) order by period")
        over = [r for r in rows if (r[ratio] or 0) > 1]
        return {
            "summary": f"{len(rows)} 個{zh}中有 {len(over)} 個{zh}比率大於 1",
            "periods_over_1": len(over),
            "rows": rows,
        }

    unit = num.measure.column
    rows = _rows(con, f"""
with n as (
{_period_select(num, grain, unit)}
),
d as (
{_period_select(den, grain, unit)}
),
nc as (
    select n.period, count(*) as numerator, count(*) filter (where d.unit is null) as outside
    from n left join d on d.period = n.period and d.unit = n.unit
    group by n.period
),
dc as (select period, count(*) as denominator from d group by period)
select period, coalesce(numerator, 0) as numerator, coalesce(denominator, 0) as denominator,
    coalesce(outside, 0) as outside,
    cast(coalesce(numerator, 0) as double) / nullif(denominator, 0) as ratio
from nc full join dc using (period)
order by period""")
    total_num = sum(r["numerator"] for r in rows)
    outside = sum(r["outside"] for r in rows)
    over = sum(1 for r in rows if (r["ratio"] or 0) > 1)
    summary = (
        f"{len(rows)} 個{zh}裡，分子共 {total_num:,} 個 {unit}（逐{zh}計），"
        f"其中 {outside:,} 個（{outside / total_num:.1%}）不在同{zh}的分母裡"
        if total_num else f"{len(rows)} 個{zh}裡分子沒有資料"
    )
    if over:
        summary += f"；有 {over} 個{zh}比率大於 1"
    return {"summary": summary, "unit": unit, "grain": grain, "numerator_total": total_num,
            "outside": outside, "periods_over_1": over, "rows": rows}


def _lineage_evidence(con: duckdb.DuckDBPyConnection, project: Project, problem: Problem) -> dict[str, Any]:
    evidence: dict[str, Any] = {"path": [hop.__dict__ for hop in problem.hops]}
    details = problem.details
    if problem.kind == "missing_property":
        event, prop = details["event"], details["property"]
        scope = "" if event == "*" else f"where event_name = {literal(event)}"
        total, with_key = con.execute(
            f"select count(*), count(*) filter (where json_exists(properties, {literal('$.' + prop)}))"
            f" from raw_events {scope}"
        ).fetchone()
        target = "所有事件" if event == "*" else f"{event} 事件"
        evidence |= {"summary": f"raw_events 裡 {total:,} 筆 {target}，帶有 '{prop}' 的有 {with_key:,} 筆",
                     "events": total, "events_with_property": with_key}
    elif problem.kind == "enum_mismatch":
        model = problem.hops[1].name.split(".")[0]
        column = details["column"]
        rows = _rows(con, f"select {column} as value, count(*) as rows from {model} group by 1 order by 2 desc")
        evidence |= {"summary": f"{model}.{column} 實際出現的值：" + "、".join(f"{r['value']}（{r['rows']:,}）" for r in rows)
                     + f"；{details['values']} 出現 0 次",
                     "rows": rows}
    else:
        evidence["summary"] = "血緣在 " + next((h.name for h in problem.hops if "✗" in h.detail), problem.subject) + " 斷掉"
    return evidence


def _attach_evidence(con: duckdb.DuckDBPyConnection, project: Project, lineage: Lineage, finding: Finding) -> None:
    ctx = finding._context
    try:
        if finding.rule == "R1":
            if ctx["metric"] in lineage.broken_metrics:
                return
            finding.evidence = rollup_comparison(con, project, ctx["metric"], ctx["from_grain"], ctx["to_grain"], ctx["agg"])
        elif finding.rule == "R2":
            if ctx["ratio"] in lineage.broken_metrics:
                return
            finding.evidence = rate_membership(con, project, ctx["ratio"], ctx["grain"])
        else:
            finding.evidence = _lineage_evidence(con, project, ctx["problem"])
    except duckdb.Error as exc:  # 證據算不出來不應該讓整個檢查失敗
        finding.evidence = {"summary": f"無法計算證據：{exc}"}


def check_project(
    project: Project, lineage: Lineage | None = None, con: duckdb.DuckDBPyConnection | None = None
) -> list[Finding]:
    """靜態檢查 R1–R3；給了倉儲連線就附上數字證據，並對靜態通過的 rate 做母體實測。"""
    lineage = lineage or resolve(project)
    findings = _r1(project) + _r2(project) + _r3(lineage)
    if con is None:
        return findings

    for finding in findings:
        _attach_evidence(con, project, lineage, finding)

    # 規格上沒問題的 rate，也實際量一次分子有沒有跑出分母
    flagged = {f.subject for f in findings if f.rule == "R2"}
    for name, metric in project.metric_layer.metrics.items():
        if not isinstance(metric, RatioMetric) or metric.kind != "rate":
            continue
        if f"metric:{name}" in flagged or name in lineage.broken_metrics:
            continue
        grain = _report_grain(project, name, "day")
        evidence = rate_membership(con, project, name, grain)
        if evidence.get("outside") or evidence.get("periods_over_1"):
            findings.append(Finding(
                "R2", "warning", "rate_leakage", f"metric:{name}",
                f"{_label(project, name)} 的規格沒有問題，但在假資料上，分子有一部分不在同期分母裡"
                f"（例如跨{GRAIN_ZH[grain]}的 session：前一{GRAIN_ZH[grain]}瀏覽、後一{GRAIN_ZH[grain]}下單）。",
                _reports_using(project, name),
                evidence,
            ))
    return findings
