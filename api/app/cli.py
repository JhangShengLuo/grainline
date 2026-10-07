"""grainline 指令列。

    python -m app.cli compile          # 只印出編譯後的 SQL
    python -m app.cli build            # 產生假資料、建立 DuckDB 倉儲、印出摘要
    python -m app.cli check            # 在假資料上跑 R1–R3，印出 finding 與數字證據
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import duckdb

from .checks import check_project
from .compiler import compile_project
from .generator import generate_events
from .lineage import resolve
from .spec import load_project
from .warehouse import build_warehouse

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SPECS = REPO_ROOT / "specs" / "shop"
DEFAULT_DB = REPO_ROOT / "data" / "shop.duckdb"


def _compile(specs: Path, plan: int | None) -> None:
    for view in compile_project(load_project(specs, plan)):
        print(f"-- [{view.layer}] {view.name}\ncreate or replace view {view.name} as\n{view.sql};\n")


def _build(specs: Path, db: Path, plan: int | None) -> None:
    started = time.perf_counter()
    project = load_project(specs, plan)
    events = generate_events(project)
    db.parent.mkdir(parents=True, exist_ok=True)
    db.unlink(missing_ok=True)
    lineage = resolve(project)
    with duckdb.connect(str(db)) as con:
        views = build_warehouse(con, project, events, lineage)
        print(f"tracking plan v{project.tracking_plan.version}，raw_events: {len(events):,} 筆合成事件 → {db}")
        for view in views:
            if view.name == "stg_identity":
                continue
            (count,) = con.execute(f"select count(*) from {view.name}").fetchone()
            print(f"  [{view.layer}] {view.name:<24} {count:>8,}")
        for view in views:
            if view.layer == "L4":
                print(f"\n{view.name}（前 8 列）")
                con.sql(f"select * from {view.name} limit 8").show(max_width=200)
        for name, reason in lineage.broken_reports.items():
            print(f"\n跳過 rpt_{name}：{reason}")
    print(f"完成，用時 {time.perf_counter() - started:.1f} 秒")


def _check(specs: Path, plan: int | None) -> int:
    project = load_project(specs, plan)
    lineage = resolve(project)
    with duckdb.connect(":memory:") as con:
        build_warehouse(con, project, generate_events(project), lineage)
        findings = check_project(project, lineage, con)
    for f in findings:
        print(f"[{f.rule} {f.severity}] {f.subject}\n  {f.message}")
        if f.impacted:
            print(f"  影響：{', '.join(f.impacted)}")
        if f.evidence and f.evidence.get("summary"):
            print(f"  證據：{f.evidence['summary']}")
        print()
    errors = sum(f.severity == "error" for f in findings)
    print(f"{errors} 個 error，{len(findings) - errors} 個 warning")
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="grainline")
    sub = parser.add_subparsers(dest="command", required=True)
    commands = (
        ("compile", "印出編譯後的 SQL"),
        ("build", "產生假資料並建立 DuckDB 倉儲"),
        ("check", "在假資料上跑 R1–R3 相容性檢查"),
    )
    for name, help_ in commands:
        p = sub.add_parser(name, help=help_)
        p.add_argument("--specs", type=Path, default=DEFAULT_SPECS)
        p.add_argument("--plan", type=int, default=None, help="tracking plan 版本（預設 status: current）")
        if name == "build":
            p.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args(argv)
    if args.command == "compile":
        _compile(args.specs, args.plan)
    elif args.command == "check":
        raise SystemExit(_check(args.specs, args.plan))
    else:
        _build(args.specs, args.db, args.plan)


if __name__ == "__main__":
    main()
