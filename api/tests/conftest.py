from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
import pytest

from app.spec import Project, load_project, read_spec_files

SHOP_SPECS = Path(__file__).resolve().parents[2] / "specs" / "shop"


@pytest.fixture
def project() -> Project:
    return load_project(SHOP_SPECS)


@pytest.fixture
def raw_specs() -> dict[str, Any]:
    """範例規格的原始 dict，方便測試改壞其中一處。"""
    return read_spec_files(SHOP_SPECS)


@pytest.fixture
def con() -> Iterator[duckdb.DuckDBPyConnection]:
    with duckdb.connect(":memory:") as connection:
        yield connection


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
