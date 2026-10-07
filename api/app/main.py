"""Grainline API：相容性檢查、數字證據、血緣與報表。

規格檔有變動時，下一個請求會重新產生假資料並重建倉儲（約 1 秒），
所以改完 YAML 直接重新整理就能看到新數字。
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
from fastapi import FastAPI, HTTPException, Query
from pydantic import ValidationError

from .checks import Finding, check_project, effective_additivity
from .generator import generate_events
from .lineage import Lineage, metric_lineage, resolve
from .spec import Project, load_project
from .warehouse import build_warehouse

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SPECS = REPO_ROOT / "specs" / "shop"


@dataclass
class State:
    project: Project
    lineage: Lineage
    con: duckdb.DuckDBPyConnection
    findings: list[Finding]
    events: int
    built_at: datetime = field(default_factory=datetime.now)


class Workspace:
    def __init__(self, spec_dir: Path) -> None:
        self.spec_dir = spec_dir
        self.lock = threading.Lock()
        self._fingerprint: tuple[Any, ...] | None = None
        self._state: State | None = None

    def _current_fingerprint(self) -> tuple[Any, ...]:
        return tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in sorted(self.spec_dir.glob("*.yaml")))

    def state(self) -> State:
        """呼叫端必須持有 self.lock。"""
        fingerprint = self._current_fingerprint()
        if fingerprint != self._fingerprint or self._state is None:
            try:
                project = load_project(self.spec_dir)
            except ValidationError as exc:
                raise HTTPException(
                    422,
                    {"message": "規格驗證失敗", "errors": [str(e["msg"]) for e in exc.errors()]},
                ) from exc
            lineage = resolve(project)
            con = duckdb.connect(":memory:")
            events = generate_events(project)
            build_warehouse(con, project, events, lineage)
            if self._state is not None:
                self._state.con.close()
            self._state = State(project, lineage, con, check_project(project, lineage, con), len(events))
            self._fingerprint = fingerprint
        return self._state


def _rows(con: duckdb.DuckDBPyConnection, sql: str) -> list[dict[str, Any]]:
    cursor = con.execute(sql)
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def create_app(spec_dir: Path | None = None) -> FastAPI:
    workspace = Workspace(spec_dir or Path(os.environ.get("GRAINLINE_SPECS", DEFAULT_SPECS)))
    app = FastAPI(title="Grainline API")

    @app.get("/health")
    def health() -> dict[str, str]:
        with duckdb.connect(":memory:") as con:
            con.execute("select 1").fetchone()
        return {"status": "ok", "duckdb": "ok"}

    @app.get("/checks")
    def checks(rule: str | None = Query(None, pattern="^R[123]$")) -> dict[str, Any]:
        with workspace.lock:
            state = workspace.state()
            findings = [f.to_dict() for f in state.findings if rule is None or f.rule == rule]
        return {
            "project": state.project.name,
            "built_at": state.built_at.isoformat(timespec="seconds"),
            "generated_events": state.events,
            "summary": {
                "error": sum(f["severity"] == "error" for f in findings),
                "warning": sum(f["severity"] == "warning" for f in findings),
            },
            "findings": findings,
        }

    @app.get("/metrics")
    def metrics() -> list[dict[str, Any]]:
        with workspace.lock:
            state = workspace.state()
        project = state.project
        return [
            {
                "name": name,
                "label": metric.label,
                "type": metric.type,
                "declared_additivity": getattr(metric, "additivity", None),
                "additivity": effective_additivity(project, name),
                "broken": state.lineage.broken_metrics.get(name),
            }
            for name, metric in project.metric_layer.metrics.items()
        ]

    @app.get("/metrics/{name}/lineage")
    def lineage(name: str) -> dict[str, Any]:
        with workspace.lock:
            state = workspace.state()
        if name not in state.project.metric_layer.metrics:
            raise HTTPException(404, f"沒有指標 {name}")
        return metric_lineage(state.project, state.lineage, name)

    @app.get("/reports")
    def reports() -> list[dict[str, Any]]:
        with workspace.lock:
            state = workspace.state()
        return [
            {
                "name": name,
                "label": report.label,
                "time_grain": report.time_grain,
                "dimensions": report.dimensions,
                "metrics": report.metrics,
                "broken": state.lineage.broken_reports.get(name),
            }
            for name, report in state.project.metric_layer.reports.items()
        ]

    @app.get("/reports/{name}")
    def report(name: str) -> dict[str, Any]:
        with workspace.lock:
            state = workspace.state()
            spec = state.project.metric_layer.reports.get(name)
            if spec is None:
                raise HTTPException(404, f"沒有報表 {name}")
            if name in state.lineage.broken_reports:
                raise HTTPException(409, {"message": "報表血緣斷掉，無法建立", "reason": state.lineage.broken_reports[name]})
            rows = _rows(state.con, f"select * from rpt_{name}")
        return {"name": name, "label": spec.label, "time_grain": spec.time_grain,
                "dimensions": spec.dimensions, "metrics": spec.metrics, "rows": rows}

    return app


app = create_app()
