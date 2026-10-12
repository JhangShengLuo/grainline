"""M12：後端資料是真相（訂單主檔、折扣碼兩張表）與 R4 業務規則、對帳。"""

from collections import Counter
from typing import Any

import duckdb
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

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


def _r4(project: Project, world: World) -> list[Any]:
    con = duckdb.connect()
    lineage = resolve(project)
    build_warehouse(con, project, world, lineage)
    return [f for f in check_project(project, lineage, con) if f.rule == "R4"]


# ---------- 模擬出來的後端資料 ----------


def test_order_amounts_add_up(world: World) -> None:
    for o in world.tables["orders"]:
        assert o["total"] == pytest.approx(o["subtotal"] - o["discount_amount"] + o["shipping_fee"])
        assert o["shipping_fee"] == (0 if o["subtotal"] >= 999 else 60)
    assert {o["status"] for o in world.tables["orders"]} == {"paid", "cancelled", "refunded"}


def test_coupon_tables_are_consistent(world: World) -> None:
    orders = {o["order_id"]: o for o in world.tables["orders"]}
    codes = {c["coupon_code"] for c in world.tables["coupons"]}
    for r in world.tables["coupon_redemptions"]:
        assert r["coupon_code"] in codes
        assert orders[r["order_id"]]["discount_amount"] == r["discount_amount"]
        assert orders[r["order_id"]]["coupon_code"] == r["coupon_code"]
        assert r["status"] == ("reversed" if orders[r["order_id"]]["status"] == "cancelled" else "applied")


def test_backend_is_the_same_under_every_tracking_plan(world: World) -> None:
    v2 = simulate(load_small(2))
    for table in ("orders", "coupons", "coupon_redemptions"):
        assert v2.tables[table] == world.tables[table]


def test_backend_orders_do_not_move_between_tracking_plans(client: TestClient) -> None:
    metrics = {m["name"]: m for m in client.get("/diff").json()["metrics"]}
    assert metrics["orders"]["delta"] == 0 and metrics["revenue"]["delta"] == 0
    assert metrics["orders_reported"]["delta"] != 0


# ---------- R4 業務規則 ----------


def test_r4_catches_the_simulated_backend_bugs(world: World) -> None:
    findings = {f.kind: f for f in _r4(load_small(), world)}
    expired = findings["rule_within"]
    assert expired.subject == "source:coupon_redemptions.redeemed_at"
    assert {r["key"] for r in expired.evidence["rows"]} == {"SUMMER15"}  # 只有過期的那個
    assert "model:fct_coupon_redemptions" in expired.impacted
    per_user = findings["rule_limit"]
    assert all(r["used"] > r["allowed"] for r in per_user.evidence["rows"])
    # 參照完整與金額一致：資料本身沒問題，不能誤報
    assert "rule_reference" not in findings and "rule_match" not in findings


def test_r4_is_silent_without_the_bugs() -> None:
    project = load_small()
    clean = project.model_copy(update={"simulation": project.simulation.model_copy(
        update={"backend_bugs": project.simulation.backend_bugs.model_copy(
            update={"accept_expired_coupon": 0.0, "skip_per_user_limit": 0.0})})})
    assert [f.kind for f in _r4(clean, simulate(clean)) if f.severity == "error"] == []


def test_r4_reference_and_match_violations(world: World) -> None:
    orders = [dict(o) for o in world.tables["orders"]]
    orders[0]["coupon_code"] = "GHOST99"       # 主檔裡沒有的折扣碼
    with_coupon = next(o for o in orders if o["discount_amount"] > 0)
    with_coupon["discount_amount"] += 1         # 和使用紀錄對不起來
    broken = World(world.events, {**world.tables, "orders": orders}, world.truth)
    findings = {f.kind: f for f in _r4(load_small(), broken)}
    assert findings["rule_reference"].evidence["rows"][0]["value"] == "GHOST99"
    assert findings["rule_match"].evidence["violations"] == 1


def test_per_user_limit_ignores_guests(world: World) -> None:
    # 沒登入的人 user_id 是空的，不能被當成同一個人計數
    findings = {f.kind: f for f in _r4(load_small(), world)}
    assert all(r["user_id"] for r in findings["rule_limit"].evidence["rows"])


def test_rule_references_are_validated(raw_specs: dict[str, Any]) -> None:
    rules = raw_specs["sources"]["coupon_redemptions"]["rules"]
    rules.append({"type": "within", "time": "redeemed_at", "key": "coupon_code", "source": "coupons",
                  "start": "starts_on", "end": "valid_until", "why": "x"})
    with pytest.raises(ValidationError, match="starts_on"):
        Project.model_validate(raw_specs)
    rules[-1] = {"type": "limit", "group_by": ["user_id"], "key": "coupon_code", "source": "coupons",
                 "max": "usage_limit", "why": "x"}
    with pytest.raises(ValidationError, match="group_by"):
        Project.model_validate(raw_specs)


# ---------- 對帳 ----------


def test_reconciliation_is_clean_in_v1_and_flags_v2(world: World) -> None:
    assert not [f for f in _r4(load_small(), world) if f.kind == "reconcile_gap"]
    v2 = load_small(2)
    gaps = {f.subject: f for f in _r4(v2, simulate(v2)) if f.kind == "reconcile_gap"}
    assert "metric:orders_reported" in gaps
    assert gaps["metric:orders_reported"].severity == "warning"


def test_checks_api_filters_r4(client: TestClient) -> None:
    findings = client.get("/checks", params={"rule": "R4"}).json()["findings"]
    assert findings and {f["rule"] for f in findings} == {"R4"}


def test_coupon_report_uses_backend_data(client: TestClient) -> None:
    rows = client.get("/reports/promotion_weekly").json()["rows"]
    used = Counter(r["coupon_code"] for r in rows if r["discount_cost"])
    assert {"FALL10", "APP50", "VIP200"} <= set(used)
