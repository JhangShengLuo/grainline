"""M6：業務資料來源、曝光、有真相的市場。"""

import json
from collections import Counter
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from app.checks import check_project
from app.generator import World, simulate
from app.lineage import resolve
from app.spec import Project
from app.warehouse import build_warehouse

from .conftest import load_small
from .test_api import client, data_dir, spec_dir  # noqa: F401  共用 fixture


@pytest.fixture(scope="module")
def world() -> World:
    return simulate(load_small())


@pytest.fixture
def shop(con: duckdb.DuckDBPyConnection, project: Project, world: World) -> duckdb.DuckDBPyConnection:
    build_warehouse(con, project, world)
    return con


# ---------- 市場的真相 ----------


def test_featured_products_get_far_more_impressions(world: World) -> None:
    truth = {t["product_id"]: t for t in world.truth}
    featured = sorted(truth.values(), key=lambda t: t["legacy_rank"])[:8]
    others = [t for t in truth.values() if t not in featured]
    assert min(t["impressions"] for t in featured) > 3 * max(t["impressions"] for t in others)


def test_potential_products_are_good_but_buried(world: World) -> None:
    potential = [t for t in world.truth if t["is_potential"]]
    assert potential
    median_impressions = sorted(t["impressions"] for t in world.truth)[len(world.truth) // 2]
    top_quarter_value = sorted((t["value_per_view"] for t in world.truth), reverse=True)[len(world.truth) // 4 - 1]
    for t in potential:
        # 每次瀏覽的預期毛利在前 25%，曝光在中位數以下
        assert t["value_per_view"] >= top_quarter_value and t["impressions"] <= median_impressions


def test_better_converting_products_sell_more_per_view(shop: duckdb.DuckDBPyConnection, world: World) -> None:
    """真相要真的反映在資料上，否則選品劇本沒有東西可以找。"""
    truth = {t["product_id"]: t["conversion"] for t in world.truth}
    rows = shop.execute("""
        select v.product_id, count(*) as detail_views, coalesce(any_value(u.units), 0) as units
        from fct_product_views v
        left join (select product_id, sum(quantity) as units from fct_order_lines group by 1) u using (product_id)
        group by 1 having count(*) >= 50""").fetchall()
    ranked = sorted(rows, key=lambda r: truth[r[0]])
    low, high = ranked[: len(ranked) // 3], ranked[-len(ranked) // 3:]
    rate = lambda group: sum(r[2] for r in group) / sum(r[1] for r in group)  # noqa: E731
    assert rate(high) > 2 * rate(low)


def test_truth_never_leaves_the_simulator(client: TestClient) -> None:
    for path in ("/catalog", "/events", "/models", "/metrics", "/reports/daily_overview"):
        body = json.dumps(client.get(path).json())
        assert '"conversion"' not in body and "is_potential" not in body and "attractiveness" not in body


# ---------- 業務資料來源 ----------


def test_sources_are_loaded_with_contract_columns(shop: duckdb.DuckDBPyConnection) -> None:
    columns = [r[0] for r in shop.execute("select column_name from (describe src_order_lines)").fetchall()]
    assert columns[:3] == ["order_id", "line_no", "product_id"]
    types = dict(shop.execute("select column_name, column_type from (describe src_products)").fetchall())
    assert types["launch_date"] == "DATE" and types["cost"] == "DOUBLE"


def test_order_lines_get_person_id_from_identity_rules(shop: duckdb.DuckDBPyConnection) -> None:
    # 有登入的訂單用會員帳號；同一裝置登入過的匿名訂單也會歸到會員
    unmatched = shop.execute("""
        select count(*) from stg_src_order_lines l
        where l.person_id not in (select person_id from stg_events)""").fetchone()[0]
    assert unmatched == 0


def test_inventory_never_negative_and_restocks(shop: duckdb.DuckDBPyConnection, world: World) -> None:
    on_hand = [r["on_hand"] for r in world.tables["inventory_daily"]]
    assert min(on_hand) >= 0
    days = {r["date"] for r in world.tables["inventory_daily"]}
    assert len(days) == 28
    # 每 7 天補貨：補貨日的庫存不會比前一天少
    per_product: dict[str, list[int]] = {}
    for r in sorted(world.tables["inventory_daily"], key=lambda r: r["date"]):
        per_product.setdefault(r["product_id"], []).append(r["on_hand"])
    assert any(series[7] > series[6] for series in per_product.values() if len(series) > 7)


def test_new_products_launch_during_the_period(world: World) -> None:
    launched = [t for t in world.truth if t["launch_day"] > 0]
    assert launched
    first_day = Counter(r["product_id"] for r in world.tables["inventory_daily"] if r["date"] == "2026-09-01")
    assert all(t["product_id"] not in first_day for t in launched)


def test_backend_data_is_the_same_under_every_tracking_plan(world: World) -> None:
    v2 = simulate(load_small(2))
    assert v2.tables["order_lines"] == world.tables["order_lines"]
    assert v2.truth == world.truth


def test_frontend_orders_match_backend_only_in_v1(con: duckdb.DuckDBPyConnection, shop: duckdb.DuckDBPyConnection) -> None:
    v1 = shop.execute("select sum(orders_reported), sum(orders_created) from rpt_frontend_vs_backend").fetchone()
    assert v1[0] == v1[1]
    v2_project = load_small(2)
    other = duckdb.connect()
    build_warehouse(other, v2_project, simulate(v2_project))
    v2 = other.execute("select sum(orders_reported), sum(orders_created) from rpt_frontend_vs_backend").fetchone()
    assert v2[0] != v2[1]  # 在成功頁送訂單：有人沒等到、有人重新整理


def test_impressions_follow_the_plan(world: World) -> None:
    impressions = [e for e in world.events if e["event_name"] == "product_impression"]
    assert {e["properties"]["list_name"] for e in impressions} == {"home_featured", "category"}
    assert min(e["properties"]["position"] for e in impressions) == 1


def test_source_grain_is_checked(project: Project, world: World, con: duckdb.DuckDBPyConnection) -> None:
    lines = world.tables["order_lines"]
    broken = World(world.events, {**world.tables, "order_lines": lines + lines[:3]}, world.truth)
    lineage = resolve(project)
    build_warehouse(con, project, broken, lineage)
    finding = next(f for f in check_project(project, lineage, con) if f.subject == "source:order_lines")
    assert (finding.rule, finding.kind) == ("R1", "grain_duplicate")
    assert finding.evidence["extra_rows"] == 3


# ---------- API ----------


def test_catalog_has_featured_ranks(client: TestClient) -> None:
    catalog = client.get("/catalog").json()
    featured = [p for p in catalog if p["featured_rank"]]
    assert [p["featured_rank"] for p in featured] == list(range(1, 9))
    assert catalog == sorted(catalog, key=lambda p: p["legacy_rank"])


def test_backend_metric_explains_its_source(client: TestClient) -> None:
    body = client.get("/metrics/ordered_product_sales/explain").json()
    assert "後端" in body["steps"][0]["text"] and "不受前端埋點設計影響" in body["steps"][0]["text"]
    assert body["example"]["rows_source"] == "order_lines"
    assert body["example"]["rows"]


def test_models_page_lists_sources(client: TestClient) -> None:
    sources = {s["name"]: s for s in client.get("/models").json()["sources"]}
    assert sources["order_lines"]["owner"] == "後端"
    assert sources["order_lines"]["grain_check"]["ok"] is True
    lineage = client.get("/metrics/units_ordered/lineage").json()
    measure = next(f for f in lineage["fields"] if f["role"] == "measure")
    assert measure["hops"][-1]["name"] == "order_lines.quantity"
