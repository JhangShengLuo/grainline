from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.ingest import IncomingEvent, plan_warnings
from app.main import create_app
from app.spec import Project

from .test_api import client, data_dir, spec_dir  # noqa: F401  共用 fixture


def _event(name: str, event_id: str, **properties: Any) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "event_name": name,
        "timestamp": "2026-10-07T02:00:00Z",  # 台北時間 10:00
        "anonymous_id": "anon-live-1",
        "user_id": None,
        "tracking_plan_version": 1,
        "properties": {"platform": "web", **properties},
    }


def _today_orders(client: TestClient) -> int:
    rows = client.get("/reports/daily_overview").json()["rows"]
    return next((r["orders"] for r in rows if r["period"] == "2026-10-07"), 0)


# ---------- 和 L1 比對 ----------


def test_conforming_event_has_no_warnings(project: Project) -> None:
    event = IncomingEvent.model_validate(_event("add_to_cart", "e1", product_id="p001", price=399, quantity=1))
    assert plan_warnings(project.tracking_plan, event) == []


@pytest.mark.parametrize(
    ("name", "properties", "expected"),
    [
        ("wishlist_add", {}, "stg_unknown_events"),
        ("add_to_cart", {"product_id": "p001", "price": 399, "quantity": 1, "color": "red"}, "color 沒有在 L1 宣告"),
        ("add_to_cart", {"product_id": "p001", "price": 399}, "缺少 property quantity"),
        ("add_to_cart", {"product_id": "p001", "price": "399", "quantity": 1}, "price 應為 number"),
        ("add_to_cart", {"product_id": "p001", "price": 399, "quantity": 0}, "小於 L1 的下限"),
        ("page_view", {"page_type": "search"}, "不在 L1 enum"),
    ],
)
def test_mismatches_are_reported(project: Project, name: str, properties: dict[str, Any], expected: str) -> None:
    event = IncomingEvent.model_validate(_event(name, "e1", **properties))
    assert any(expected in m for m in plan_warnings(project.tracking_plan, event))


def test_timestamp_is_converted_to_taipei_time() -> None:
    event = IncomingEvent.model_validate(_event("page_view", "e1", page_type="home"))
    assert event.to_raw()["timestamp"] == "2026-10-07 10:00:00"


# ---------- /ingest ----------


def test_ingested_order_shows_up_in_report_immediately(client: TestClient) -> None:
    assert _today_orders(client) == 0
    response = client.post("/ingest", json={"events": [
        _event("page_view", "e1", page_type="home"),
        _event("order_completed", "e2", order_id="o-live-1", revenue=1200, item_count=2),
    ]}).json()
    assert response == {"accepted": 2, "duplicates": 0, "warnings": []}
    assert _today_orders(client) == 1


def test_resending_the_same_events_is_idempotent(client: TestClient) -> None:
    batch = {"events": [_event("order_completed", "e2", order_id="o-live-1", revenue=1200, item_count=2)]}
    client.post("/ingest", json=batch)
    assert client.post("/ingest", json=batch).json()["duplicates"] == 1
    assert _today_orders(client) == 1


def test_unknown_event_is_stored_but_quarantined(client: TestClient) -> None:
    body = client.post("/ingest", json={"events": [_event("wishlist_add", "e9", product_id="p001")]}).json()
    assert body["accepted"] == 1
    assert "stg_unknown_events" in body["warnings"][0]["messages"][0]
    live = client.get("/events/live").json()
    assert live[0]["event_name"] == "wishlist_add"
    assert live[0]["quarantined"] is True


def test_live_events_show_l2_parsing(client: TestClient) -> None:
    client.post("/ingest", json={"events": [
        _event("page_view", "e1", page_type="home"),
        {**_event("login", "e2", method="email"), "user_id": "demo-amy", "timestamp": "2026-10-07T02:01:00Z"},
    ]})
    live = client.get("/events/live").json()
    # 身分合併回溯生效：登入前的 page_view 也歸到 demo-amy
    assert {e["person_id"] for e in live} == {"demo-amy"}
    assert len({e["session_id"] for e in live}) == 1
    assert live[0]["properties"]["platform"] == "web"


def test_live_events_survive_spec_rebuild(client: TestClient, spec_dir: Path) -> None:
    client.post("/ingest", json={"events": [_event("order_completed", "e2", order_id="o1", revenue=100, item_count=1)]})
    l2 = spec_dir / "l2_staging.yaml"
    l2.write_text(l2.read_text(encoding="utf-8").replace("session_timeout_minutes: 30", "session_timeout_minutes: 45"),
                  encoding="utf-8")
    assert _today_orders(client) == 1


def test_live_events_survive_app_restart(spec_dir: Path, data_dir: Path) -> None:
    TestClient(create_app(spec_dir, data_dir)).post(
        "/ingest", json={"events": [_event("order_completed", "e2", order_id="o1", revenue=100, item_count=1)]}
    )
    assert _today_orders(TestClient(create_app(spec_dir, data_dir))) == 1


def test_clear_live_events(client: TestClient) -> None:
    client.post("/ingest", json={"events": [_event("order_completed", "e2", order_id="o1", revenue=100, item_count=1)]})
    assert client.delete("/events/live").json() == {"deleted": 1}
    assert _today_orders(client) == 0
    assert client.get("/events/live").json() == []


def test_live_events_refresh_check_evidence(client: TestClient) -> None:
    def outside() -> int:
        findings = client.get("/checks", params={"rule": "R2"}).json()["findings"]
        return next(f for f in findings if f["subject"] == "metric:web_conversion_rate")["evidence"]["outside"]

    before = outside()
    # app 上的買家：在 web 轉換率的分子裡，但不在 web UV 分母裡
    app_order = _event("order_completed", "e5", order_id="o-app", revenue=100, item_count=1)
    app_order["properties"]["platform"] = "app"
    app_order["anonymous_id"] = "anon-live-app"
    client.post("/ingest", json={"events": [app_order]})
    assert outside() == before + 1


def test_malformed_envelope_is_rejected(client: TestClient) -> None:
    bad = _event("page_view", "e1", page_type="home")
    del bad["anonymous_id"]
    assert client.post("/ingest", json={"events": [bad]}).status_code == 422
    assert client.post("/ingest", json={"events": []}).status_code == 422


def test_catalog_and_tracking_plan(client: TestClient) -> None:
    catalog = client.get("/catalog").json()
    assert len(catalog) == 60
    assert {"id", "name", "category", "price"} <= catalog[0].keys()
    plan = client.get("/tracking-plan").json()
    assert plan["version"] == 1
    assert "add_to_cart" in plan["events"]
