"""DuckDB 倉儲：不可變的 raw_events，加上編譯出來的 view。"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import duckdb

from .compiler import View, apply_views, compile_project
from .lineage import Lineage
from .spec import Project

RAW_COLUMNS = {
    "event_id": "VARCHAR",
    "event_name": "VARCHAR",
    "timestamp": "TIMESTAMP",
    "anonymous_id": "VARCHAR",
    "user_id": "VARCHAR",
    "tracking_plan_version": "INTEGER",
    "properties": "JSON",
}
# 事件從哪裡來：sim（模擬器）或 live（demo 商店的真實點擊）。這是 ingest 的中繼資料，不屬於 L1。
SOURCES = ("sim", "live")


def create_raw_events(con: duckdb.DuckDBPyConnection) -> None:
    columns = ", ".join(f"{name} {type_}" for name, type_ in RAW_COLUMNS.items())
    con.execute(f"create table if not exists raw_events ({columns}, source VARCHAR)")


def load_events(con: duckdb.DuckDBPyConnection, events: Iterable[dict[str, Any]], source: str = "sim") -> int:
    """把事件附加進 raw_events（只新增、不修改），回傳新增筆數。"""
    assert source in SOURCES
    create_raw_events(con)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "events.ndjson"
        count = 0
        with path.open("w", encoding="utf-8") as f:
            for event in events:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
                count += 1
        if count == 0:
            return 0
        names = ", ".join(RAW_COLUMNS)
        types = "{" + ", ".join(f"'{n}': '{t}'" for n, t in RAW_COLUMNS.items()) + "}"
        con.execute(
            f"insert into raw_events select {names}, '{source}' "
            f"from read_json(?, format = 'newline_delimited', columns = {types})",
            [str(path)],
        )
    return count


def build_warehouse(
    con: duckdb.DuckDBPyConnection,
    project: Project,
    events: Iterable[dict[str, Any]],
    lineage: Lineage | None = None,
) -> list[View]:
    load_events(con, events)
    views = compile_project(project, lineage)
    apply_views(con, views)
    return views
