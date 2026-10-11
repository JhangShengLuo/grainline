"""M9：模擬執行。"""

import duckdb
import pytest
from fastapi.testclient import TestClient

from app.execution import simulate_execution
from app.generator import Scenario, World, simulate
from app.lineage import resolve
from app.spec import Project, load_project
from app.warehouse import build_warehouse

from .conftest import SHOP_SPECS, load_small
from .test_api import client, data_dir, spec_dir  # noqa: F401  共用 fixture


@pytest.fixture(scope="module")
def demo() -> tuple[Project, duckdb.DuckDBPyConnection, World]:
    project = load_project(SHOP_SPECS)
    world = simulate(project)
    con = duckdb.connect()
    build_warehouse(con, project, world)
    return project, con, world


def test_scenarios_keep_the_product_world(demo: tuple[Project, duckdb.DuckDBPyConnection, World]) -> None:
    project, _, world = demo
    future = simulate(project, Scenario(behavior_seed=1, offset_days=28, days=7))
    assert future.tables["products"] == world.tables["products"]
    assert future.events == []  # 不產生前端事件
    assert min(r["ordered_at"] for r in future.tables["order_lines"]) >= "2026-09-29"


def test_same_seed_same_scenario(demo: tuple[Project, duckdb.DuckDBPyConnection, World]) -> None:
    project, _, _ = demo
    a = simulate(project, Scenario(behavior_seed=3, offset_days=28, days=7))
    b = simulate(project, Scenario(behavior_seed=3, offset_days=28, days=7))
    assert a.tables["order_lines"] == b.tables["order_lines"]


def test_featured_override_moves_exposure(demo: tuple[Project, duckdb.DuckDBPyConnection, World]) -> None:
    project, _, _ = demo
    control = simulate(project, Scenario(behavior_seed=3, offset_days=28, days=7))
    target = "p016"
    treated = simulate(project, Scenario(behavior_seed=3, offset_days=28, days=7, featured=(target,)))
    before = next(t["impressions"] for t in control.truth if t["product_id"] == target)
    after = next(t["impressions"] for t in treated.truth if t["product_id"] == target)
    assert after > 3 * before


def test_execution_reports_effects_with_ranges_and_costs(demo: tuple[Project, duckdb.DuckDBPyConnection, World]) -> None:
    project, con, _ = demo
    result = simulate_execution(project, resolve(project), con, "potential_products", replicates=3, horizon_days=7)
    assert result["status"] == "ready"
    margin = result["effects"]["playbook"]["margin"]
    assert margin["min"] <= margin["mean"] <= margin["max"]
    assert len(result["effects"]["playbook"]["per_run"]["margin"]) == 3
    # 選得好的部分：劇本比隨機挑好
    assert result["skill"]["margin"]["mean"] > 0
    # 被換下的商品要誠實顯示損失
    displaced = [p for p in result["products"] if p["role"] == "換下首頁"]
    assert displaced and all(p["exposure_after"] < p["exposure_before"] for p in displaced)
    assert sum(p["margin_change"] for p in displaced) < 0
    for section in ("assumptions", "method", "uncertainty", "limitations"):
        assert result["explanation"][section]


def test_execution_is_blocked_without_the_data() -> None:
    project = load_small(2)
    result = simulate_execution(project, resolve(project), duckdb.connect(), "potential_products")
    assert result["status"] == "blocked"


def test_execution_api_is_cached(client: TestClient) -> None:
    params = {"plan": 1, "replicates": 2, "horizon_days": 7}
    first = client.get("/decisions/potential_products/execution", params=params).json()
    second = client.get("/decisions/potential_products/execution", params=params).json()
    assert first == second and first["replicates"] == 2
    assert client.get("/decisions/potential_products/execution", params={"replicates": 50}).status_code == 422
