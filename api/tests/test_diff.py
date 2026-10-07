from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.diff import metric_events, plan_changes
from app.spec import Project, load_project

from .conftest import SHOP_SPECS
from .test_api import client, data_dir, spec_dir  # noqa: F401  共用 fixture


def test_plan_changes_v1_to_v2(project: Project) -> None:
    changes = plan_changes(project.tracking_plan, load_project(SHOP_SPECS, 2).tracking_plan)
    kinds = {(c["kind"], c["event"], c["property"]) for c in changes}
    assert kinds == {
        ("event_removed", "product_view", None),
        ("fires_on_changed", "add_to_cart", None),
        ("fires_on_changed", "order_completed", None),
        ("property_added", "page_view", "product_id"),
        ("property_added", "page_view", "price"),
        ("property_added", "order_completed", "coupon_code"),
    }


def test_metric_events(project: Project) -> None:
    assert metric_events(project, "conversion_rate") == {"order_completed", "page_view"}
    assert metric_events(project, "sessions") == {"*"}


@pytest.fixture
def diff(client: TestClient) -> dict[str, Any]:
    return client.get("/diff").json()


def test_diff_defaults_to_current_vs_latest(diff: dict[str, Any]) -> None:
    assert (diff["base"]["version"], diff["target"]["version"]) == (1, 2)


def test_diff_metric_numbers(diff: dict[str, Any]) -> None:
    metrics = {m["name"]: m for m in diff["metrics"]}
    # 同樣的行為，沒改到的指標完全一樣
    assert metrics["page_views"]["delta"] == 0
    assert not metrics["page_views"]["changed"]
    # 點擊就送：失敗的點擊也算進去
    assert metrics["cart_adds"]["delta"] > 0
    assert any("add_to_cart 的送出時機" in c for c in metrics["cart_adds"]["related_changes"])
    # 移除 product_view：指標斷掉
    assert metrics["product_views"]["base"]["broken"] is None
    assert "product_view" in metrics["product_views"]["target"]["broken"]
    assert metrics["product_views"]["changed"]
    # 比率由總數重算，不是兩個版本比率的差
    aov = metrics["aov"]
    assert aov["target"]["value"] == pytest.approx(metrics["revenue"]["target"]["value"] / metrics["orders"]["target"]["value"])


def test_diff_reports_and_findings(diff: dict[str, Any]) -> None:
    reports = {r["name"]: r for r in diff["reports"]}
    assert reports["promotion_weekly"]["base_broken"] and reports["promotion_weekly"]["target_broken"] is None
    added = {(f["kind"], f["subject"]) for f in diff["findings"]["added"]}
    resolved = {(f["kind"], f["subject"]) for f in diff["findings"]["resolved"]}
    assert ("grain_duplicate", "model:fct_orders") in added
    assert ("missing_event", "fct_product_views") in added
    assert ("missing_property", "fct_orders.coupon_code") in resolved


def test_diff_ignores_demo_shop_events(client: TestClient) -> None:
    client.post("/ingest", json={"events": [_order("o-live", 1)]})
    metrics = {m["name"]: m for m in client.get("/diff").json()["metrics"]}
    assert metrics["page_views"]["delta"] == 0
    # 報表本身仍然包含 demo 商店的點擊
    assert _orders_on(client, 1) == 1


def test_diff_with_unknown_version(client: TestClient) -> None:
    assert client.get("/diff", params={"target": 9}).status_code == 404


# ---------- 多版本 API ----------


def test_tracking_plans_list(client: TestClient) -> None:
    plans = client.get("/tracking-plans").json()
    assert [(p["version"], p["status"], p["default"]) for p in plans] == [(1, "current", True), (2, "proposal", False)]


def test_tracking_plan_exposes_fires_on_and_from(client: TestClient) -> None:
    plan = client.get("/tracking-plan", params={"plan": 2}).json()
    order = plan["events"]["order_completed"]
    assert order["fires_on"] == ["order_complete_page_load"]
    assert order["properties"]["coupon_code"]["from"] == "order.coupon_code"


def test_endpoints_take_plan_param(client: TestClient) -> None:
    assert client.get("/reports/promotion_weekly", params={"plan": 2}).status_code == 200
    assert client.get("/reports/promotion_weekly", params={"plan": 1}).status_code == 409
    assert client.get("/checks", params={"plan": 2}).json()["tracking_plan_version"] == 2
    assert client.get("/metrics", params={"plan": 9}).status_code == 404


def _order(event_id: str, version: int) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "event_name": "order_completed",
        "timestamp": "2026-10-08T02:00:00Z",
        "anonymous_id": "anon-live",
        "user_id": None,
        "tracking_plan_version": version,
        "properties": {"platform": "web", "order_id": event_id, "revenue": 100, "item_count": 1},
    }


def _orders_on(client: TestClient, version: int) -> int:
    rows = client.get("/reports/daily_overview", params={"plan": version}).json()["rows"]
    return next((r["orders"] for r in rows if r["period"] == "2026-10-08"), 0)


def test_live_events_go_to_their_own_version(client: TestClient) -> None:
    client.post("/ingest", json={"events": [_order("o-v2", 2)]})
    assert (_orders_on(client, 1), _orders_on(client, 2)) == (0, 1)
    report = client.get("/reports/daily_overview", params={"plan": 2}).json()
    assert report["live_periods"] == ["2026-10-08"]


def test_ingest_with_unknown_version_warns(client: TestClient) -> None:
    body = client.post("/ingest", json={"events": [_order("o-v7", 7)]}).json()
    assert body["accepted"] == 1
    assert "v7 不存在" in body["warnings"][0]["messages"][0]
