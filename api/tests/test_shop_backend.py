"""M13：demo 商店付款時由後端建立訂單。"""

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

from .conftest import copy_small_specs

NOW = datetime(2026, 10, 7, 10, 0, 0)


@pytest.fixture
def shop(tmp_path: Path) -> TestClient:
    return TestClient(create_app(copy_small_specs(tmp_path / "shop"), tmp_path / "data", clock=lambda: NOW))


def _order(**overrides: Any) -> dict[str, Any]:
    return {"anonymous_id": "anon-demo", "user_id": "demo-amy", "platform": "web",
            "lines": [{"product_id": "p001", "quantity": 2}], **overrides}


def _today(client: TestClient, column: str) -> float:
    rows = client.get("/reports/frontend_vs_backend").json()["rows"]
    return next((r[column] for r in rows if r["period"] == "2026-10-07"), 0)


def test_order_is_priced_by_the_backend(shop: TestClient) -> None:
    price = next(p["price"] for p in shop.get("/catalog").json() if p["id"] == "p001")
    body = shop.post("/shop/orders", json=_order()).json()
    assert body["subtotal"] == price * 2
    assert body["shipping_fee"] == (0 if price * 2 >= 999 else 60)
    assert body["total"] == body["subtotal"] + body["shipping_fee"]
    assert _today(shop, "orders") == 1 and _today(shop, "revenue") == body["total"]


@pytest.mark.parametrize(
    ("code", "message"),
    [("FALL10", "已在 2026-09-30 過期"), ("NOPE", "沒有這個折扣碼"), ("VIP200", "未達最低消費 NT$ 1,500")],
)
def test_coupons_are_validated_correctly(shop: TestClient, code: str, message: str) -> None:
    cheapest = min(shop.get("/catalog").json(), key=lambda p: p["price"])
    assert cheapest["price"] < 1500
    body = shop.post("/shop/orders", json=_order(coupon_code=code, lines=[{"product_id": cheapest["id"], "quantity": 1}])).json()
    coupon = body["coupon"]
    assert not coupon["accepted"] and message in coupon["message"]
    assert body["discount_amount"] == 0


def test_per_user_limit_is_enforced(shop: TestClient) -> None:
    big = _order(coupon_code="app50", lines=[{"product_id": "p001", "quantity": 5}])
    first = shop.post("/shop/orders", json=big).json()
    assert first["coupon"]["accepted"] and first["discount_amount"] == 50
    second = shop.post("/shop/orders", json=big).json()
    assert not second["coupon"]["accepted"] and "每人限用 1 次" in second["coupon"]["message"]


def test_frontend_event_plus_backend_order_reconciles(shop: TestClient) -> None:
    body = shop.post("/shop/orders", json=_order()).json()
    shop.post("/ingest", json={"events": [{
        "event_id": "e-order", "event_name": "order_completed", "timestamp": "2026-10-07T02:00:00Z",
        "anonymous_id": "anon-demo", "user_id": "demo-amy", "tracking_plan_version": 1,
        "properties": {"platform": "web", "order_id": body["order_id"], "revenue": body["subtotal"], "item_count": 2},
    }]})
    assert _today(shop, "orders_reported") == _today(shop, "orders_created") == 1
    gaps = [f for f in shop.get("/checks", params={"rule": "R4"}).json()["findings"] if f["kind"] == "reconcile_gap"]
    assert not gaps


def test_backend_orders_survive_rebuild_and_appear_in_every_plan(tmp_path: Path) -> None:
    spec_dir = copy_small_specs(tmp_path / "shop")
    first = TestClient(create_app(spec_dir, tmp_path / "data", clock=lambda: NOW))
    first.post("/shop/orders", json=_order())
    again = TestClient(create_app(spec_dir, tmp_path / "data", clock=lambda: NOW))
    for plan in (1, 2):
        rows = again.get("/reports/frontend_vs_backend", params={"plan": plan}).json()["rows"]
        assert next(r["orders_created"] for r in rows if r["period"] == "2026-10-07") == 1


def test_clearing_demo_data_removes_backend_orders(shop: TestClient) -> None:
    shop.post("/shop/orders", json=_order())
    assert _today(shop, "orders") == 1
    shop.delete("/events/live")
    assert _today(shop, "orders") == 0


def test_unknown_product_is_rejected(shop: TestClient) -> None:
    assert shop.post("/shop/orders", json=_order(lines=[{"product_id": "p999", "quantity": 1}])).status_code == 422
