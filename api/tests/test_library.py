"""M7：lookup／expr、漏斗比率、行銷指標庫與覆蓋檢查。"""

from typing import Any

import duckdb
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.checks import check_project
from app.compiler import compile_project
from app.generator import simulate
from app.library import coverage, load_library
from app.lineage import resolve
from app.spec import Project, expr_columns
from app.warehouse import build_warehouse

from .conftest import load_small
from .test_api import client, data_dir, spec_dir  # noqa: F401  共用 fixture


@pytest.fixture
def shop(con: duckdb.DuckDBPyConnection, project: Project) -> duckdb.DuckDBPyConnection:
    build_warehouse(con, project, simulate(project))
    return con


# ---------- lookup 與 expr ----------


def test_gross_margin_uses_product_cost(shop: duckdb.DuckDBPyConnection) -> None:
    wrong = shop.execute("""
        select count(*) from fct_order_lines l join dim_products p using (product_id)
        where l.unit_cost <> p.cost or abs(l.gross_margin - (l.line_revenue - l.quantity * p.cost)) > 1e-6
    """).fetchone()[0]
    assert wrong == 0
    # lookup 不會讓列數變多
    assert shop.execute("select count(*) from fct_order_lines").fetchone()[0] == \
        shop.execute("select count(*) from src_order_lines").fetchone()[0]


def test_lookup_lineage_reaches_the_cost_source(project: Project) -> None:
    info = resolve(project).columns["fct_order_lines"]["unit_cost"]
    assert info.type == "number"
    assert info.hops[-1].name == "products.cost"


def test_lookup_on_multi_column_grain_is_a_fan_out(raw_specs: dict[str, Any]) -> None:
    raw_specs["models"]["fct_order_lines"]["columns"]["stock"] = {
        "lookup": {"model": "fct_inventory_daily", "column": "on_hand", "by": "product_id"}
    }
    project = Project.model_validate(raw_specs)
    finding = next(f for f in check_project(project) if f.kind == "lookup_fan_out")
    assert (finding.rule, finding.subject) == ("R1", "fct_order_lines.stock")
    assert "一對多" in finding.message
    assert "stock" not in resolve(project).usable_columns("fct_order_lines")


@pytest.mark.parametrize("expr", ["line_revenue; drop table x", "quantity ** 2", "max(quantity)"])
def test_expr_rejects_anything_but_arithmetic(expr: str) -> None:
    with pytest.raises(ValueError):
        expr_columns(expr)


def test_expr_must_reference_columns_of_the_same_model(raw_specs: dict[str, Any]) -> None:
    raw_specs["models"]["fct_order_lines"]["columns"]["gross_margin"]["expr"] = "line_revenue - discount"
    with pytest.raises(ValidationError, match="discount"):
        Project.model_validate(raw_specs)


def test_expr_on_text_column_is_a_type_error(raw_specs: dict[str, Any]) -> None:
    raw_specs["models"]["fct_order_lines"]["columns"]["gross_margin"]["expr"] = "line_revenue - coupon_code"
    project = Project.model_validate(raw_specs)
    assert any(f.kind == "type_mismatch" and f.subject == "fct_order_lines.gross_margin" for f in check_project(project))
    assert "gross_margin" in resolve(project).broken_metrics


def test_lookup_cycle_is_rejected(raw_specs: dict[str, Any]) -> None:
    raw_specs["models"]["dim_products"]["columns"]["x"] = {
        "lookup": {"model": "fct_order_lines", "column": "quantity", "by": "product_id"}
    }
    with pytest.raises(ValidationError, match="循環"):
        Project.model_validate(raw_specs)


def test_lookup_target_is_compiled_first(project: Project) -> None:
    names = [v.name for v in compile_project(project)]
    assert names.index("dim_products") < names.index("fct_order_lines")


# ---------- 漏斗比率 ----------


def test_funnel_ratios_are_not_treated_as_subsets(shop: duckdb.DuckDBPyConnection, project: Project) -> None:
    lineage = resolve(project)
    findings = check_project(project, lineage, shop)
    assert not [f for f in findings if f.rule == "R2" and f.subject in (
        "metric:product_ctr", "metric:add_to_cart_rate", "metric:unit_session_percentage")
        and f.severity == "error"]


def test_product_report_finds_a_buried_converter(shop: duckdb.DuckDBPyConnection) -> None:
    """報表上就看得到：有商品曝光很少，轉換率卻比被推的商品高。"""
    rows = shop.execute("""
        select product_id, sum(product_impressions) as imps,
               sum(units_ordered) / nullif(sum(product_detail_views), 0) as conv
        from rpt_product_weekly group by 1 having sum(product_detail_views) >= 30""").fetchall()
    featured = sorted(rows, key=lambda r: -r[1])[:8]
    buried = [r for r in rows if r[1] < min(f[1] for f in featured) / 3]
    assert max(r[2] for r in buried) > max(f[2] for f in featured)


# ---------- 指標庫 ----------


def test_library_loads_and_every_dependency_exists() -> None:
    library = load_library()
    assert {"product_impressions", "unit_session_percentage", "gross_margin"} <= set(library.metrics)


def test_v1_covers_the_whole_library(project: Project) -> None:
    statuses = {c["name"]: c["status"] for c in coverage(project, resolve(project))}
    assert set(statuses.values()) == {"available"}


def test_v2_coverage_says_what_is_missing_and_who_owns_it() -> None:
    v2 = load_small(2)
    items = {c["name"]: c for c in coverage(v2, resolve(v2))}
    assert items["product_detail_views"]["status"] == "blocked"
    assert items["product_detail_views"]["missing"][0]["owner"] == "前端"
    assert items["cart_adds"]["status"] == "differs"
    assert "add_to_cart_success" in items["cart_adds"]["missing"][0]["what"]
    # 比率繼承分子分母缺的東西
    assert {m["kind"] for m in items["add_to_cart_rate"]["missing"]} == {"event", "fires_on"}
    assert items["gross_margin"]["status"] == "available"


def test_missing_source_column_names_the_owner(raw_specs: dict[str, Any]) -> None:
    del raw_specs["sources"]["products"]["columns"]["cost"]
    raw_specs["models"]["dim_products"]["columns"].pop("cost")
    project = Project.model_validate(raw_specs)
    gross = next(c for c in coverage(project, resolve(project)) if c["name"] == "gross_margin")
    assert gross["status"] == "blocked"
    assert gross["missing"] == [{"kind": "source_column", "owner": "商品／營運", "source": "products",
                                 "column": "cost", "what": "業務資料 products 加上欄位 cost"}]


def test_library_api(client: TestClient) -> None:
    v2 = client.get("/library", params={"plan": 2}).json()
    assert v2["summary"]["blocked"] >= 3 and v2["summary"]["differs"] == 1
    assert v2["groups"]["funnel"] == "商品漏斗"
    v1 = client.get("/library", params={"plan": 1}).json()
    assert v1["summary"]["available"] == len(v1["metrics"])


def test_product_report_is_broken_in_v2(client: TestClient) -> None:
    assert client.get("/reports/product_weekly", params={"plan": 1}).status_code == 200
    assert client.get("/reports/product_weekly", params={"plan": 2}).status_code == 409
