"""M8：L5 潛力商品劇本、反推、提案、用真相評估。"""

from pathlib import Path
from typing import Any

import duckdb
import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.decisions import build_proposal, evaluate, requirements, run_decision
from app.generator import World, simulate
from app.lineage import resolve
from app.spec import Project, TrackingPlan, load_project
from app.warehouse import build_warehouse

from .conftest import SHOP_SPECS, load_small
from .test_api import client, data_dir, spec_dir  # noqa: F401  共用 fixture


@pytest.fixture(scope="module")
def v1() -> tuple[Project, duckdb.DuckDBPyConnection, World]:
    project = load_small()
    world = simulate(project)
    con = duckdb.connect()
    build_warehouse(con, project, world)
    return project, con, world


def test_playbook_recommends_buried_profitable_products(v1: tuple[Project, duckdb.DuckDBPyConnection, World]) -> None:
    project, con, _ = v1
    result = run_decision(project, resolve(project), con, "potential_products")
    assert result["status"] == "ready"
    recommended = result["recommended"]
    assert 0 < len(recommended) <= 5
    median = next(r["threshold"] for r in result["rules"] if r["input"] == "impressions")
    for c in recommended:
        assert c["passed"] and c["values"]["impressions"] <= median and c["values"]["views"] >= 20
        assert c["name"]  # 有商品名稱
    scores = [c["score"] for c in recommended]
    assert scores == sorted(scores, reverse=True)


def test_shrinkage_pulls_small_samples_toward_the_average(v1: tuple[Project, duckdb.DuckDBPyConnection, World]) -> None:
    project, con, _ = v1
    result = run_decision(project, resolve(project), con, "potential_products")
    overall = result["overall"]["rate"]
    for c in result["candidates"]:
        if c["raw_rate"] is not None and c["values"]["views"] > 0:
            # 收斂後的轉換率一定介於原始轉換率與全站平均之間
            low, high = sorted([c["raw_rate"], overall])
            assert low - 1e-9 <= c["rate"] <= high + 1e-9


def test_as_of_uses_only_earlier_data(v1: tuple[Project, duckdb.DuckDBPyConnection, World]) -> None:
    project, con, _ = v1
    lineage = resolve(project)
    early = run_decision(project, lineage, con, "potential_products", project.simulation.start_date.replace(day=8))
    full = run_decision(project, lineage, con, "potential_products")
    assert early["window"]["end"] == "2026-09-07"
    assert early["overall"]["totals"]["impressions"] < full["overall"]["totals"]["impressions"]


@pytest.fixture(scope="module")
def demo_size() -> tuple[Project, duckdb.DuckDBPyConnection, World]:
    """選品效果要用 demo 規模（1,500 個訪客）驗證：小模擬裡曝光少的商品只有十幾次瀏覽。"""
    project = load_project(SHOP_SPECS)
    world = simulate(project)
    con = duckdb.connect()
    build_warehouse(con, project, world)
    return project, con, world


def test_playbook_beats_traditional_picks(demo_size: tuple[Project, duckdb.DuckDBPyConnection, World]) -> None:
    project, con, world = demo_size
    result = evaluate(project, resolve(project), con, "potential_products", world.truth)
    assert result["potential"]
    playbook = result["playbook"]["precision"]
    assert playbook >= 0.6
    for baseline in result["baselines"].values():
        assert playbook > baseline["precision"]
    assert [c["day"] for c in result["curve"]] == list(range(7, 29))


def test_requirements_trace_down_to_page_behaviors(v1: tuple[Project, duckdb.DuckDBPyConnection, World]) -> None:
    project, _, _ = v1
    req = requirements(project, resolve(project), "potential_products")
    assert req["ready"]
    layers = req["layers"]
    assert {"fct_product_impressions", "fct_order_lines", "dim_products"} <= set(layers["models"])
    assert {s["source"] for s in layers["sources"]} == {"order_lines", "products", "inventory_daily"}
    where = {m["moment"]: m["where"] for m in layers["moments"]}
    assert where["product_impression"] == "首頁精選、分類頁的商品卡片"
    assert where["product_detail_load"] == "商品頁"


def test_v2_is_blocked_and_says_what_to_add() -> None:
    project = load_small(2)
    lineage = resolve(project)
    req = requirements(project, lineage, "potential_products")
    assert not req["ready"]
    assert req["missing_by_owner"] == {"前端": ["新增事件 product_view（在 product_detail_load 時送出，帶 product_id）"]}
    assert run_decision(project, lineage, duckdb.connect(), "potential_products")["status"] == "blocked"


def test_proposal_fixes_v2(spec_dir: Path) -> None:
    project = load_small(2)
    proposal = build_proposal(spec_dir, project, resolve(project), "potential_products")
    assert proposal["needed"] and proposal["version"] == 3
    raw = yaml.safe_load(proposal["yaml"])
    plan = TrackingPlan.model_validate(raw)
    assert plan.events["product_view"].fires_on == ["product_detail_load"]
    assert plan.events["product_view"].properties["product_id"].from_ == "product.id"
    # 其他事件保持 v2 原樣
    assert plan.events["order_completed"].fires_on == ["order_complete_page_load"]
    fixed = project.model_copy(update={"tracking_plan": plan})
    assert requirements(fixed, resolve(fixed), "potential_products")["ready"]


def test_v1_needs_no_proposal(spec_dir: Path) -> None:
    project = load_small()
    assert build_proposal(spec_dir, project, resolve(project), "potential_products")["changes"] == []


def test_playbook_inputs_must_be_additive(raw_specs: dict[str, Any]) -> None:
    raw_specs["decisions"]["potential_products"]["inputs"]["views"] = "add_to_cart_rate"
    project = Project.model_validate(raw_specs)
    req = requirements(project, resolve(project), "potential_products")
    views = next(i for i in req["inputs"] if i["alias"] == "views")
    assert views["status"] == "blocked" and "不可加" in views["problems"][0]


def test_rules_must_use_declared_inputs(raw_specs: dict[str, Any]) -> None:
    raw_specs["decisions"]["potential_products"]["rules"].append({"input": "returns", "op": "lt", "value": 3, "why": "x"})
    with pytest.raises(ValidationError, match="returns"):
        Project.model_validate(raw_specs)


@pytest.mark.parametrize("value", ["p101", "median", "50%"])
def test_percentile_thresholds_are_validated(raw_specs: dict[str, Any], value: str) -> None:
    raw_specs["decisions"]["potential_products"]["rules"][0]["value"] = value
    with pytest.raises(ValidationError):
        Project.model_validate(raw_specs)


# ---------- API ----------


def test_decision_api_round_trip(client: TestClient, spec_dir: Path) -> None:
    assert client.get("/decisions", params={"plan": 2}).json()[0]["ready"] is False
    assert client.get("/decisions/potential_products", params={"plan": 1}).json()["status"] == "ready"
    preview = client.get("/decisions/potential_products/proposal", params={"plan": 2}).json()
    assert preview["version"] == 3 and not (spec_dir / "tracking_plans" / "v3.yaml").exists()

    created = client.post("/decisions/potential_products/proposal", params={"plan": 2}).json()
    assert created["filename"] == "tracking_plans/v3.yaml"
    assert (spec_dir / "tracking_plans" / "v3.yaml").exists()
    # 寫入後倉儲自動重建：v3 可以執行劇本、可以評估
    versions = [p["version"] for p in client.get("/tracking-plans").json()]
    assert versions == [1, 2, 3]
    assert client.get("/decisions/potential_products", params={"plan": 3}).json()["status"] == "ready"
    evaluation = client.get("/decisions/potential_products/evaluation", params={"plan": 3}).json()
    assert evaluation["status"] == "ready"
    assert client.post("/decisions/potential_products/proposal", params={"plan": 1}).status_code == 409


def test_evaluation_reveals_labels_but_not_truth_values(client: TestClient) -> None:
    body = client.get("/decisions/potential_products/evaluation", params={"plan": 1}).text
    assert "potential" in body
    assert "value_per_view" not in body and "attractiveness" not in body


def test_unknown_decision(client: TestClient) -> None:
    assert client.get("/decisions/nope").status_code == 404
