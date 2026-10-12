import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
import pytest

from app.spec import Project, load_project, read_spec_files

SHOP_SPECS = Path(__file__).resolve().parents[2] / "specs" / "shop"
# 測試用比較小的模擬（demo 用 1,500 個訪客），結構和比例都一樣，只是快很多
TEST_VISITORS = 400


def small(project: Project) -> Project:
    return project.model_copy(
        update={"simulation": project.simulation.model_copy(update={"visitors": TEST_VISITORS})}
    )


def load_small(version: int | None = None) -> Project:
    return small(load_project(SHOP_SPECS, version))


def copy_small_specs(target: Path) -> Path:
    """複製範例規格到 target，並把模擬改小（API 測試會改這份檔案）。"""
    shutil.copytree(SHOP_SPECS, target)
    sim = target / "simulation.yaml"
    sim.write_text(sim.read_text(encoding="utf-8").replace("visitors: 1500", f"visitors: {TEST_VISITORS}"), encoding="utf-8")
    return target


@pytest.fixture
def project() -> Project:
    return load_small()


@pytest.fixture
def raw_specs() -> dict[str, Any]:
    """範例規格的原始 dict，方便測試改壞其中一處。"""
    return read_spec_files(SHOP_SPECS)


@pytest.fixture
def con() -> Iterator[duckdb.DuckDBPyConnection]:
    with duckdb.connect(":memory:") as connection:
        yield connection


def backend_order(order_id: str, created_at: str, anonymous_id: str, total: float, status: str = "paid",
                  user_id: str | None = None, platform: str = "web") -> dict[str, Any]:
    """手工的後端訂單（訂單主檔一列）。"""
    return {
        "order_id": order_id, "anonymous_id": anonymous_id, "user_id": user_id, "platform": platform,
        "status": status, "item_count": 1, "subtotal": total, "discount_amount": 0.0, "shipping_fee": 0.0,
        "total": total, "coupon_code": None, "created_at": created_at,
    }


def world(events: list[dict[str, Any]], orders: list[dict[str, Any]] | None = None) -> Any:
    """手工事件加上手工的後端訂單。"""
    from app.generator import World

    return World(events=events, tables={"orders": orders or []}, truth=[])


def event(
    name: str,
    timestamp: str,
    anonymous_id: str = "anon-1",
    user_id: str | None = None,
    event_id: str | None = None,
    **properties: Any,
) -> dict[str, Any]:
    """手工事件，給需要精確數字的測試使用。"""
    return {
        "event_id": event_id or f"{anonymous_id}-{timestamp}-{name}",
        "event_name": name,
        "timestamp": timestamp,
        "anonymous_id": anonymous_id,
        "user_id": user_id,
        "tracking_plan_version": 1,
        "properties": {"platform": "web", **properties},
    }
