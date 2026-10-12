"""M14：匯出成 Apache Ossie（OSI）與 Cube。"""

import io
import json
import zipfile

import duckdb
import pytest
import yaml
from fastapi.testclient import TestClient

from app.checks import check_project
from app.generator import simulate
from app.lineage import resolve
from app.standards import export_cube, export_osi, to_yaml, validate_osi

from .conftest import load_small
from .test_api import client, data_dir, spec_dir  # noqa: F401  共用 fixture


def _doc(version: int = 1) -> dict:
    from app.warehouse import build_warehouse

    project = load_small(version)
    lineage = resolve(project)
    con = duckdb.connect()
    build_warehouse(con, project, simulate(project), lineage)
    return export_osi(project, lineage, check_project(project, lineage, con))


@pytest.fixture(scope="module")
def doc() -> dict:
    return _doc()


def _ext(item: dict) -> dict:
    return json.loads(next(e["data"] for e in item["custom_extensions"] if e["vendor_name"] == "GRAINLINE"))


@pytest.mark.parametrize("version", [1, 2])
def test_export_passes_the_official_schema(version: int) -> None:
    assert validate_osi(_doc(version)) == []


def test_schema_validation_catches_mistakes(doc: dict) -> None:
    broken = json.loads(json.dumps(doc))
    del broken["datasets"][0]["source"]
    broken["metrics"][0]["expression"]["dialects"][0]["dialect"] = "DUCKDB"
    errors = validate_osi(broken)
    assert any("source" in e for e in errors) and any("DUCKDB" in e for e in errors)


def test_datasets_carry_grain_and_lineage(doc: dict) -> None:
    orders = next(d for d in doc["datasets"] if d["name"] == "fct_orders")
    assert orders["primary_key"] == ["order_id"]
    created = next(f for f in orders["fields"] if f["name"] == "created_at")
    assert created["datatype"] == "DateTime" and created["dimension"]["is_time"] is True
    total = next(f for f in orders["fields"] if f["name"] == "total")
    assert _ext(total)["lineage"][-1]["name"] == "orders.total"


def test_lookups_become_relationships(doc: dict) -> None:
    rels = {(r["from"], r["to"]): r for r in doc["relationships"]}
    assert rels[("fct_order_lines", "dim_products")]["from_columns"] == ["product_id"]
    assert rels[("fct_order_lines", "fct_orders")]["to_columns"] == ["order_id"]


def test_metric_expressions_keep_filters_and_ratios(doc: dict) -> None:
    metrics = {m["name"]: m for m in doc["metrics"]}
    revenue = metrics["revenue"]["expression"]["dialects"][0]["expression"]
    assert revenue == "SUM(CASE WHEN fct_orders.status IN ('paid', 'refunded') THEN fct_orders.total END)"
    aov = metrics["aov"]["expression"]["dialects"][0]["expression"]
    assert "NULLIF(COUNT(CASE WHEN fct_orders.status" in aov
    assert _ext(metrics["uv"])["additivity"] == "non_additive"
    assert _ext(metrics["unit_session_percentage"])["library"]["reference"].startswith("Amazon")


def test_what_the_standard_cannot_express_is_listed_not_dropped(doc: dict) -> None:
    model = _ext(doc)
    assert [s["metric"] for s in model["not_exported"]] == ["weekly_uv_from_daily"]
    assert "potential_products" in model["decisions"]
    assert model["business_sources"]["coupon_redemptions"]["rules"]
    # 問題也跟著指標走
    web = _ext(next(m for m in doc["metrics"] if m["name"] == "web_conversion_rate"))
    assert any(f["rule"] == "R2" for f in web["findings"])


def test_v2_skips_metrics_with_broken_lineage() -> None:
    skipped = {s["metric"] for s in _ext(_doc(2))["not_exported"]}
    assert {"product_detail_views", "product_ctr"} <= skipped


def test_cube_export_uses_the_official_converter(doc: dict) -> None:
    files, notes = export_cube(doc, "fct_orders")
    assert "model/cubes/fct_orders.yml" in files and any(p.startswith("model/views/") for p in files)
    cube = yaml.safe_load(files["model/cubes/fct_orders.yml"])["cubes"][0]
    revenue = next(m for m in cube["measures"] if m["name"] == "revenue")
    assert revenue["type"] == "sum" and "status IN" in revenue["sql"]
    # 和指標同名的欄位在 Cube 裡改名，並說明
    assert any("gross_margin_value" in n for n in notes)
    # 轉換器誠實說明：跨資料表的比率需要 join 路徑
    assert any("conversion_rate" in n and "APPROXIMATED" in n for n in notes)
    # OSI 本身沒有被改
    assert any(f["name"] == "gross_margin" for d in doc["datasets"] if d["name"] == "fct_order_lines" for f in d["fields"])


def test_export_yaml_round_trips(doc: dict) -> None:
    assert yaml.safe_load(to_yaml(doc)) == doc


def test_export_api(client: TestClient) -> None:
    summary = client.get("/export/summary").json()
    assert summary["valid"] and summary["standard"]["name"] == "Apache Ossie"
    assert summary["cube"]["ok"] and summary["counts"]["metrics"] > 20
    osi = client.get("/export/osi.yaml")
    assert osi.headers["content-type"].startswith("application/yaml")
    assert yaml.safe_load(osi.text)["version"] == "0.2.0.dev0"
    archive = zipfile.ZipFile(io.BytesIO(client.get("/export/cube.zip").content))
    assert "model/cubes/fct_orders.yml" in archive.namelist() and "CONVERSION_NOTES.md" in archive.namelist()
