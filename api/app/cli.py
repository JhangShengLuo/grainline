"""grainline 指令列。

    python -m app.cli compile          # 只印出編譯後的 SQL
    python -m app.cli build            # 產生假資料、建立 DuckDB 倉儲、印出摘要
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import duckdb

from .compiler import compile_project
from .generator import generate_events
from .spec import load_project
from .warehouse import build_warehouse

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SPECS = REPO_ROOT / "specs" / "shop"
DEFAULT_DB = REPO_ROOT / "data" / "shop.duckdb"


def _compile(specs: Path) -> None:
    for view in compile_project(load_project(specs)):
        print(f"-- [{view.layer}] {view.name}\ncreate or replace view {view.name} as\n{view.sql};\n")


def _build(specs: Path, db: Path) -> None:
    started = time.perf_counter()
    project = load_project(specs)
    events = generate_events(project)
    db.parent.mkdir(parents=True, exist_ok=True)
    db.unlink(missing_ok=True)
    with duckdb.connect(str(db)) as con:
        views = build_warehouse(con, project, events)
        print(f"raw_events: {len(events):,} 筆合成事件 → {db}")
        for view in views:
            if view.name == "stg_identity":
                continue
            (count,) = con.execute(f"select count(*) from {view.name}").fetchone()
            print(f"  [{view.layer}] {view.name:<24} {count:>8,}")
        for view in views:
            if view.layer == "L4":
                print(f"\n{view.name}（前 8 列）")
                con.sql(f"select * from {view.name} limit 8").show(max_width=200)
    print(f"完成，用時 {time.perf_counter() - started:.1f} 秒")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="grainline")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_ in (("compile", "印出編譯後的 SQL"), ("build", "產生假資料並建立 DuckDB 倉儲")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--specs", type=Path, default=DEFAULT_SPECS)
        if name == "build":
            p.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args(argv)
    if args.command == "compile":
        _compile(args.specs)
    else:
        _build(args.specs, args.db)


if __name__ == "__main__":
    main()
