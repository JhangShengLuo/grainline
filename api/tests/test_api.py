from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

from .conftest import copy_small_specs


@pytest.fixture
def spec_dir(tmp_path: Path) -> Path:
    return copy_small_specs(tmp_path / "shop")


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def client(spec_dir: Path, data_dir: Path) -> TestClient:
    return TestClient(create_app(spec_dir, data_dir))


def test_checks_returns_findings_with_evidence(client: TestClient) -> None:
    body = client.get("/checks").json()
    # 三個刻意的衝突（R1–R3），加上後端 bug 造成的 R4 違規
    assert {f["rule"] for f in body["findings"] if f["severity"] == "error"} == {"R1", "R2", "R3", "R4"}
    assert body["generated_events"] > 0
    for finding in body["findings"]:
        assert finding["evidence"]["summary"]


def test_checks_filter_by_rule(client: TestClient) -> None:
    findings = client.get("/checks", params={"rule": "R1"}).json()["findings"]
    assert {f["rule"] for f in findings} == {"R1"}
    assert client.get("/checks", params={"rule": "R9"}).status_code == 422


def test_metrics_show_effective_additivity(client: TestClient) -> None:
    metrics = {m["name"]: m for m in client.get("/metrics").json()}
    assert metrics["uv"]["additivity"] == "non_additive"
    assert metrics["weekly_uv_from_daily"]["additivity"] == "non_additive"
    assert metrics["orders"]["additivity"] == "additive"


def test_metric_lineage(client: TestClient) -> None:
    body = client.get("/metrics/conversion_rate/lineage").json()
    assert body["numerator"]["metric"] == "buyers"
    assert client.get("/metrics/nope/lineage").status_code == 404


def test_report_rows_and_broken_report(client: TestClient) -> None:
    body = client.get("/reports/daily_overview").json()
    assert len(body["rows"]) == 28
    assert body["rows"][0]["period"] == "2026-09-01"
    assert body["metrics"][0] == {"name": "uv", "label": "不重複訪客", "additivity": "non_additive", "format": "number"}
    # 折扣碼報表改用後端資料，v1 就能建立；v2 沒有 product_view，商品報表建不起來
    assert client.get("/reports/promotion_weekly").status_code == 200
    broken = client.get("/reports/product_weekly", params={"plan": 2})
    assert broken.status_code == 409
    assert "product_view" in broken.json()["detail"]["reason"]


def test_editing_specs_rebuilds_on_next_request(client: TestClient, spec_dir: Path) -> None:
    def coupon_conflict() -> bool:
        return any(f["subject"] == "fct_order_events.coupon_code" for f in client.get("/checks").json()["findings"])

    assert coupon_conflict()
    # 前端補上 coupon_code 埋點 → R3 衝突消失
    l1 = spec_dir / "tracking_plans" / "v1.yaml"
    l1.write_text(
        l1.read_text(encoding="utf-8").replace(
            "      item_count: {type: integer, min: 1, from: cart.count}\n\n  product_impression:",
            "      item_count: {type: integer, min: 1, from: cart.count}\n"
            "      coupon_code: {type: string, from: order.coupon_code}\n\n  product_impression:",
        ),
        encoding="utf-8",
    )
    assert not coupon_conflict()


def test_invalid_spec_returns_422(client: TestClient, spec_dir: Path) -> None:
    l2 = spec_dir / "l2_staging.yaml"
    l2.write_text("session_timeout_minutes: -5\n", encoding="utf-8")
    response = client.get("/checks")
    assert response.status_code == 422
    assert response.json()["detail"]["errors"]
