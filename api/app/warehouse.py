"""DuckDB 倉儲：原始 hit 和後端表是不可變的表，其他都是 models.py 裡的 view。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import duckdb

from . import models
from .sim import World

TABLES = {
    "raw_hits": "hit_id BIGINT, ts VARCHAR, url VARCHAR, source VARCHAR",
    "src_orders": "order_id VARCHAR, uid VARCHAR, created_at TIMESTAMP, total DOUBLE, item_count INTEGER",
    "src_order_lines": "order_id VARCHAR, line_no INTEGER, sku VARCHAR, qty INTEGER, unit_price DOUBLE, line_revenue DOUBLE",
    "src_products": "sku VARCHAR, name VARCHAR, cat VARCHAR, price DOUBLE, cost DOUBLE",
}


def insert(con: duckdb.DuckDBPyConnection, table: str, rows: list[dict]) -> None:
    if not rows:
        return
    columns = dict(c.split() for c in TABLES[table].split(", "))
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"{table}.ndjson"
        with path.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps({c: row.get(c) for c in columns}, ensure_ascii=False) + "\n")
        types = "{" + ", ".join(f"'{c}': '{t}'" for c, t in columns.items()) + "}"
        con.execute(f"insert into {table} select {', '.join(columns)} "
                    f"from read_json(?, format = 'newline_delimited', columns = {types})", [str(path)])


def build(world: World) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    for table, columns in TABLES.items():
        con.execute(f"create table {table} ({columns})")
    insert(con, "raw_hits", world.hits)
    insert(con, "src_orders", world.orders)
    insert(con, "src_order_lines", world.order_lines)
    insert(con, "src_products", [vars(p) for p in world.products])
    models.apply(con)
    return con
