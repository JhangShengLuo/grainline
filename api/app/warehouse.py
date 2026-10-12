"""DuckDB 倉儲：不可變的 raw_events，加上編譯出來的 view。"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import duckdb

from .compiler import View, apply_views, compile_project
from .generator import World
from .lineage import Lineage
from .spec import Project, Source

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


SOURCE_TYPES = {
    "string": "VARCHAR",
    "integer": "BIGINT",
    "number": "DOUBLE",
    "boolean": "BOOLEAN",
    "timestamp": "TIMESTAMP",
    "date": "DATE",
}


def load_source(con: duckdb.DuckDBPyConnection, name: str, source: Source, rows: list[dict[str, Any]],
                replace: bool = True) -> int:
    """業務資料來源載入成 src_<name> 表；只取合約宣告的欄位，資料裡沒有的欄位是 NULL。replace=False 時附加。"""
    columns = ", ".join(f"{col} {SOURCE_TYPES[c.type]}" for col, c in source.columns.items())
    con.execute(f"create {'or replace ' if replace else ''}table {'' if replace else 'if not exists '}src_{name} ({columns})")
    if not rows:
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"{name}.ndjson"
        with path.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps({k: row.get(k) for k in source.columns}, ensure_ascii=False) + "\n")
        types = "{" + ", ".join(f"'{col}': '{SOURCE_TYPES[c.type]}'" for col, c in source.columns.items()) + "}"
        con.execute(
            f"insert into src_{name} select {', '.join(source.columns)} "
            f"from read_json(?, format = 'newline_delimited', columns = {types})",
            [str(path)],
        )
    return len(rows)


def build_warehouse(
    con: duckdb.DuckDBPyConnection,
    project: Project,
    world: World | Iterable[dict[str, Any]],
    lineage: Lineage | None = None,
) -> list[View]:
    """載入事件與業務資料，再建立 L2–L4 view。也接受只有事件的 list（測試用）。"""
    if isinstance(world, World):
        load_events(con, world.events)
        tables = world.tables
    else:
        load_events(con, world)
        tables = {}
    create_raw_events(con)
    for name, source in project.sources.items():
        load_source(con, name, source, tables.get(name, []))
    views = compile_project(project, lineage)
    apply_views(con, views)
    return views
