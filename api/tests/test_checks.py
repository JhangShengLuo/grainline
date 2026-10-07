from typing import Any

import duckdb
import pytest

from app.checks import Finding, check_project, effective_additivity, rate_membership, rollup_comparison
from app.generator import generate_events
from app.lineage import metric_lineage, resolve
from app.spec import Project
from app.warehouse import build_warehouse

from .conftest import event


def _keys(findings: list[Finding]) -> set[tuple[str, str, str, str]]:
    return {(f.rule, f.severity, f.kind, f.subject) for f in findings}


def _project(raw: dict[str, Any]) -> Project:
    return Project.model_validate(raw)


# ---------- 範例規格裡刻意放的三個衝突 ----------


def test_static_checks_find_the_three_deliberate_conflicts(project: Project) -> None:
    assert _keys(check_project(project)) == {
        ("R1", "error", "rollup_non_additive", "metric:weekly_uv_from_daily"),
        ("R2", "error", "rate_filter", "metric:web_conversion_rate"),
        ("R3", "error", "missing_property", "fct_orders.coupon_code"),
    }


@pytest.fixture
def shop_findings(con: duckdb.DuckDBPyConnection, project: Project) -> dict[str, Finding]:
    lineage = resolve(project)
    build_warehouse(con, project, generate_events(project), lineage)
    return {f.subject: f for f in check_project(project, lineage, con)}


def test_r1_evidence_shows_overcount(shop_findings: dict[str, Finding]) -> None:
    evidence = shop_findings["metric:weekly_uv_from_daily"].evidence
    assert evidence is not None
    assert evidence["to_grain"] == "week"
    assert evidence["total_rolled"] > evidence["total_direct"] > 0
    assert evidence["diff"] == sum(r["diff"] for r in evidence["rows"])
    assert "重複計算" in evidence["summary"]


def test_r2_evidence_counts_numerator_outside_denominator(shop_findings: dict[str, Finding]) -> None:
    evidence = shop_findings["metric:web_conversion_rate"].evidence
    assert evidence is not None
    # 分子沒有 platform = web，app 的買家都在分母外
    assert 0 < evidence["outside"] < evidence["numerator_total"]


def test_r3_evidence_shows_frontend_never_sends_the_property(shop_findings: dict[str, Finding]) -> None:
    finding = shop_findings["fct_orders.coupon_code"]
    assert finding.impacted == ["report:promotion_weekly"]
    assert finding.evidence is not None
    assert finding.evidence["events"] > 0
    assert finding.evidence["events_with_property"] == 0
    assert [h["layer"] for h in finding.evidence["path"]] == ["L3", "L2"]


def test_clean_rate_still_gets_measured(shop_findings: dict[str, Finding]) -> None:
    # 規格正確的 conversion_rate 只會因為跨日 session 出現 warning，不會是 error
    finding = shop_findings.get("metric:conversion_rate")
    if finding is not None:
        assert (finding.rule, finding.severity, finding.kind) == ("R2", "warning", "rate_leakage")


# ---------- 用手工事件驗證精確數字 ----------


def test_rollup_comparison_exact(con: duckdb.DuckDBPyConnection, project: Project) -> None:
    # 同一個人週一、週二各來一次：日 UV 加總 = 2，週 UV = 1
    build_warehouse(
        con,
        project,
        [
            event("page_view", "2026-09-07 10:00:00", "a", page_type="home"),
            event("page_view", "2026-09-08 10:00:00", "a", page_type="home"),
            event("page_view", "2026-09-08 11:00:00", "b", page_type="home"),
        ],
    )
    evidence = rollup_comparison(con, project, "uv", "day", "week")
    assert (evidence["total_direct"], evidence["total_rolled"], evidence["diff"]) == (2, 3, 1)


def test_rate_membership_exact(con: duckdb.DuckDBPyConnection, project: Project) -> None:
    build_warehouse(
        con,
        project,
        [
            event("page_view", "2026-09-01 10:00:00", "w", page_type="home"),
            event("order_completed", "2026-09-01 10:05:00", "w", order_id="o1", revenue=100, item_count=1),
            event("page_view", "2026-09-01 11:00:00", "m", page_type="home", platform="app"),
            event("order_completed", "2026-09-01 11:05:00", "m", order_id="o2", revenue=100, item_count=1, platform="app"),
        ],
    )
    evidence = rate_membership(con, project, "web_conversion_rate", "day")
    assert (evidence["numerator_total"], evidence["outside"], evidence["periods_over_1"]) == (2, 1, 1)


# ---------- 改壞規格：每種違規都要被抓到 ----------


def test_r1_count_distinct_declared_additive(raw_specs: dict[str, Any]) -> None:
    raw_specs["metric_layer"]["metrics"]["buyers"]["additivity"] = "additive"
    project = _project(raw_specs)
    assert ("R1", "error", "declared_additivity", "metric:buyers") in _keys(check_project(project))
    assert effective_additivity(project, "buyers") == "non_additive"


def test_r1_summing_additive_metric_is_fine(raw_specs: dict[str, Any]) -> None:
    raw_specs["metric_layer"]["metrics"]["weekly_uv_from_daily"]["of"] = "orders"
    assert not [f for f in check_project(_project(raw_specs)) if f.rule == "R1"]


def test_r1_week_does_not_nest_into_month(raw_specs: dict[str, Any]) -> None:
    metrics = raw_specs["metric_layer"]["metrics"]
    metrics["monthly_orders"] = {"type": "rollup", "label": "月訂單", "of": "orders", "from_grain": "week", "agg": "sum"}
    raw_specs["metric_layer"]["reports"]["monthly"] = {"label": "月報", "time_grain": "month", "metrics": ["monthly_orders"]}
    assert ("R1", "error", "grain_nesting", "report:monthly") in _keys(check_project(_project(raw_specs)))


def test_r2_rate_with_different_units(raw_specs: dict[str, Any]) -> None:
    raw_specs["metric_layer"]["metrics"]["conversion_rate"]["numerator"] = "orders"
    finding = next(f for f in check_project(_project(raw_specs)) if f.subject == "metric:conversion_rate")
    assert (finding.rule, finding.kind) == ("R2", "rate_unit")
    assert "fct_orders 的列" in finding.message


def test_r2_rate_of_amounts_must_be_per_unit(raw_specs: dict[str, Any]) -> None:
    raw_specs["metric_layer"]["metrics"]["aov"]["kind"] = "rate"
    assert ("R2", "error", "rate_unit", "metric:aov") in _keys(check_project(_project(raw_specs)))


def test_r2_per_unit_needs_same_population(raw_specs: dict[str, Any]) -> None:
    raw_specs["metric_layer"]["metrics"]["aov"]["denominator"] = "buyers"
    raw_specs["metric_layer"]["metrics"]["revenue"]["filters"] = [{"column": "platform", "op": "eq", "value": "app"}]
    assert ("R2", "error", "per_unit_population", "metric:aov") in _keys(check_project(_project(raw_specs)))


def test_r3_filter_value_outside_enum(raw_specs: dict[str, Any]) -> None:
    raw_specs["metric_layer"]["metrics"]["web_uv"]["filters"][0]["value"] = "ios"
    project = _project(raw_specs)
    finding = next(f for f in check_project(project) if f.kind == "enum_mismatch")
    assert finding.subject == "metric:web_uv.filter"
    assert "ios" in finding.message
    # 篩選值錯誤不會讓指標斷鏈（SQL 跑得動，只是永遠是 0）
    assert "web_uv" not in resolve(project).broken_metrics


def test_r3_sum_on_string_column(raw_specs: dict[str, Any]) -> None:
    raw_specs["metric_layer"]["metrics"]["revenue"]["measure"]["column"] = "platform"
    project = _project(raw_specs)
    assert ("R3", "error", "type_mismatch", "metric:revenue.measure") in _keys(check_project(project))
    lineage = resolve(project)
    # 斷鏈往下游傳：用到 revenue 的比率和報表都受影響
    assert {"revenue", "aov"} <= set(lineage.broken_metrics)
    assert "daily_overview" in lineage.broken_reports


def test_r3_metric_column_missing_in_model(raw_specs: dict[str, Any]) -> None:
    raw_specs["metric_layer"]["metrics"]["orders"]["filters"] = [{"column": "channel", "op": "eq", "value": "ads"}]
    assert ("R3", "error", "missing_column", "metric:orders.filter") in _keys(check_project(_project(raw_specs)))


# ---------- 血緣路徑 ----------


def test_metric_lineage_reaches_l1(project: Project) -> None:
    tree = metric_lineage(project, resolve(project), "aov")
    revenue = next(f for f in tree["numerator"]["fields"] if f["role"] == "measure")
    assert [h["layer"] for h in revenue["hops"]] == ["L4", "L3", "L2", "L1"]
    assert revenue["hops"][-1]["name"] == "order_completed.revenue"
    assert revenue["type"] == "number" and revenue["broken"] is None
