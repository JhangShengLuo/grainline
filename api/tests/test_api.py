import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

from .conftest import SHOP_SPECS


@pytest.fixture
def spec_dir(tmp_path: Path) -> Path:
    target = tmp_path / "shop"
    shutil.copytree(SHOP_SPECS, target)
    return target


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def client(spec_dir: Path, data_dir: Path) -> TestClient:
    return TestClient(create_app(spec_dir, data_dir))


def test_checks_returns_findings_with_evidence(client: TestClient) -> None:
    body = client.get("/checks").json()
    assert body["summary"]["error"] == 3
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
    broken = client.get("/reports/promotion_weekly")
    assert broken.status_code == 409
    assert "coupon_code" in broken.json()["detail"]["reason"]


def test_editing_specs_rebuilds_on_next_request(client: TestClient, spec_dir: Path) -> None:
    assert client.get("/checks").json()["summary"]["error"] == 3
    # 前端補上 coupon_code 埋點 → R3 衝突消失，報表可以建立
    l1 = spec_dir / "l1_tracking_plan.yaml"
    l1.write_text(
        l1.read_text(encoding="utf-8").replace(
            "      item_count: {type: integer, min: 1}\n\n  login:",
            "      item_count: {type: integer, min: 1}\n      coupon_code: {type: string}\n\n  login:",
        ),
        encoding="utf-8",
    )
    body = client.get("/checks").json()
    assert body["summary"]["error"] == 2
    assert client.get("/reports/promotion_weekly").status_code == 200


def test_invalid_spec_returns_422(client: TestClient, spec_dir: Path) -> None:
    l2 = spec_dir / "l2_staging.yaml"
    l2.write_text("session_timeout_minutes: -5\n", encoding="utf-8")
    response = client.get("/checks")
    assert response.status_code == 422
    assert response.json()["detail"]["errors"]
