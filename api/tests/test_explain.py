from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.explain import identity_text, metric_summary
from app.spec import Project

from .test_api import client, data_dir, spec_dir  # noqa: F401  共用 fixture


def _explain(client: TestClient, name: str, plan: int = 1, example: int = 0) -> dict[str, Any]:
    response = client.get(f"/metrics/{name}/explain", params={"plan": plan, "example": example})
    assert response.status_code == 200
    return response.json()


# ---------- 業務／行銷：指標說明 ----------


def test_summaries_are_plain_language(project: Project) -> None:
    assert metric_summary(project, "uv") == "有幾個不同的人，來自 page_view 事件"
    assert metric_summary(project, "conversion_rate") == "購買人數 ÷ 不重複訪客"
    assert metric_summary(project, "weekly_uv_from_daily") == "把每天的不重複訪客加總"
    assert "只算事件來源平台是 web" in metric_summary(project, "web_uv")


def test_simple_metric_steps_go_from_l1_to_l4(client: TestClient) -> None:
    body = _explain(client, "uv")
    assert [s["layer"] for s in body["steps"]] == ["L1", "L2", "L3", "L4"]
    assert "頁面載入完成時" in body["steps"][0]["text"]
    assert "登入前的紀錄也算同一個人" in body["steps"][1]["text"]


def test_distinct_count_example_counts_a_person_once(client: TestClient) -> None:
    example = _explain(client, "uv")["example"]
    counted = [e for e in example["events"] if "算進來" in e["marks"]]
    assert len(counted) >= 2
    assert "只算 1" in example["summary"]


def test_other_examples_are_different_people(client: TestClient) -> None:
    first = _explain(client, "uv", example=0)["example"]
    second = _explain(client, "uv", example=1)["example"]
    assert first["person_id"] != second["person_id"]
    assert second["index"] == 1


def test_ratio_example_marks_numerator_and_denominator(client: TestClient) -> None:
    body = _explain(client, "conversion_rate")
    assert [p["role"] for p in body["parts"]] == ["分子", "分母"]
    marks = {m for e in body["example"]["events"] for m in e["marks"]}
    assert marks == {"分子", "分母"}
    assert "不是把每個人的比率平均" in body["example"]["summary"]


def test_rollup_example_shows_double_counting(client: TestClient) -> None:
    body = _explain(client, "weekly_uv_from_daily")
    assert body["example"]["grain"] == "week"
    assert "直接以週計算，他算 1" in body["example"]["summary"]
    assert any(c["level"] == "error" and "不可加" in c["text"] for c in body["caveats"])


def test_v2_caveats_explain_tracking_timing(client: TestClient) -> None:
    caveats = [c["text"] for c in _explain(client, "cart_adds", plan=2)["caveats"]]
    assert any("API 失敗" in c for c in caveats)
    v1 = [c["text"] for c in _explain(client, "cart_adds", plan=1)["caveats"]]
    assert not any("API 失敗" in c for c in v1)


def test_v2_orders_example_shows_a_duplicated_order(client: TestClient) -> None:
    example = _explain(client, "orders", plan=2)["example"]
    assert "被重複記錄" in example["summary"]
    orders = [e["properties"]["order_id"] for e in example["events"] if "算進來" in e["marks"]]
    assert len(orders) > len(set(orders))


def test_caveats_only_cover_columns_the_metric_uses(client: TestClient) -> None:
    # fct_orders.coupon_code 斷掉（v1），但營收沒有用到它
    assert not any("coupon_code" in c["text"] for c in _explain(client, "revenue")["caveats"])


def test_broken_metric_explains_why(client: TestClient) -> None:
    body = _explain(client, "product_views", plan=2)
    assert body["broken"]
    assert body["steps"][0]["broken"] is True
    assert body["example"] is None


def test_metrics_list_has_summary_and_problem_count(client: TestClient) -> None:
    metrics = {m["name"]: m for m in client.get("/metrics", params={"plan": 2}).json()}
    assert metrics["orders"]["problems"] >= 2
    assert metrics["uv"]["problems"] == 0
    assert "daily_overview" in metrics["uv"]["reports"]


def test_identity_text_follows_l2(project: Project) -> None:
    other = project.staging.model_copy(update={"identity": "anonymous_only"})
    assert "一個裝置就是一個人" in identity_text(other)


# ---------- 前端／PM：埋點清單 ----------


def test_event_catalog_shows_usage_and_requests(client: TestClient) -> None:
    body = client.get("/events", params={"plan": 1}).json()
    events = {e["name"]: e for e in body["events"]}
    assert events["order_completed"]["used_by"]["models"] == ["fct_orders"]
    assert {r["name"] for r in events["order_completed"]["used_by"]["reports"]} >= {"daily_overview"}
    assert events["checkout_start"]["used_by"]["metrics"] == []
    price = next(p for p in events["add_to_cart"]["properties"] if p["name"] == "price")
    assert price["from_text"] == "商品當下售價"
    # 下游需要、L1 還沒有
    assert body["downstream_requests"][0]["property"] == "coupon_code"
    assert body["shared_models"] == ["dim_sessions", "dim_persons"]


def test_event_catalog_counts_demo_shop_events(client: TestClient) -> None:
    client.post("/ingest", json={"events": [
        {"event_id": "e1", "event_name": "page_view", "timestamp": "2026-10-08T02:00:00Z", "anonymous_id": "a",
         "user_id": None, "tracking_plan_version": 1, "properties": {"platform": "web", "page_type": "search"}},
        {"event_id": "e2", "event_name": "wishlist_add", "timestamp": "2026-10-08T02:01:00Z", "anonymous_id": "a",
         "user_id": None, "tracking_plan_version": 1, "properties": {"platform": "web"}},
    ]})
    body = client.get("/events", params={"plan": 1}).json()
    page_view = next(e for e in body["events"] if e["name"] == "page_view")["live"]
    assert (page_view["received"], page_view["with_warnings"]) == (1, 1)
    assert "不在 L1 enum" in page_view["warnings"][0]["message"]
    assert body["unknown_events"][0]["event_name"] == "wishlist_add"


# ---------- DE：資料模型 ----------


def test_model_catalog(client: TestClient) -> None:
    body = client.get("/models", params={"plan": 2}).json()
    assert "30 分鐘" in body["staging"]["session_text"]
    models = {m["name"]: m for m in body["models"]}
    assert models["fct_orders"]["grain_check"]["ok"] is False
    assert models["fct_page_views"]["grain_check"]["ok"] is True
    assert models["fct_product_views"]["broken"] and models["fct_product_views"]["sql"] is None
    revenue = next(c for c in models["fct_orders"]["columns"] if c["name"] == "revenue")
    assert revenue["type"] == "number"
    assert revenue["origin"] == {"layer": "L1", "name": "order_completed.revenue", "detail": "number"}
    assert "select" in models["fct_orders"]["sql"]


@pytest.mark.parametrize("path", ["/metrics/nope/explain", "/events?plan=9", "/models?plan=9"])
def test_not_found(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 404
