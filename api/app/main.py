"""Grainline API：相容性檢查、數字證據、血緣與報表。

規格檔有變動時，下一個請求會重新產生假資料並重建倉儲（約 1 秒），
所以改完 YAML 直接重新整理就能看到新數字。demo 商店的真實點擊另外保存，重建時一併載入。
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
from fastapi import FastAPI, HTTPException, Query
from pydantic import ValidationError

from .catalog import catalog_items
from .checks import Finding, check_project, effective_additivity
from .generator import generate_events
from .ingest import IngestBatch, LiveStore, plan_warnings
from .lineage import Lineage, metric_lineage, resolve
from .spec import Project, load_project
from .warehouse import build_warehouse, load_events

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SPECS = REPO_ROOT / "specs" / "shop"
DEFAULT_DATA = REPO_ROOT / "data"


@dataclass
class State:
    project: Project
    lineage: Lineage
    con: duckdb.DuckDBPyConnection
    events: int
    built_at: datetime = field(default_factory=datetime.now)
    _findings: list[Finding] | None = None

    def findings(self) -> list[Finding]:
        """有新的 live 事件時證據要重算，所以延遲到需要時才計算。"""
        if self._findings is None:
            self._findings = check_project(self.project, self.lineage, self.con)
        return self._findings


class Workspace:
    def __init__(self, spec_dir: Path, data_dir: Path) -> None:
        self.spec_dir = spec_dir
        self.live = LiveStore(data_dir / f"live_events_{spec_dir.name}.ndjson")
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
            load_events(con, self.live.read_all(), source="live")
            if self._state is not None:
                self._state.con.close()
            self._state = State(project, lineage, con, len(events))
            self._fingerprint = fingerprint
        return self._state


def _rows(con: duckdb.DuckDBPyConnection, sql: str) -> list[dict[str, Any]]:
    cursor = con.execute(sql)
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def create_app(spec_dir: Path | None = None, data_dir: Path | None = None) -> FastAPI:
    workspace = Workspace(
        spec_dir or Path(os.environ.get("GRAINLINE_SPECS", DEFAULT_SPECS)),
        data_dir or Path(os.environ.get("GRAINLINE_DATA", DEFAULT_DATA)),
    )
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
            findings = [f.to_dict() for f in state.findings() if rule is None or f.rule == rule]
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

    @app.get("/tracking-plan")
    def tracking_plan() -> dict[str, Any]:
        with workspace.lock:
            state = workspace.state()
        return state.project.tracking_plan.model_dump(exclude_none=True)

    @app.get("/catalog")
    def catalog() -> list[dict[str, Any]]:
        with workspace.lock:
            state = workspace.state()
        return catalog_items(state.project)

    @app.post("/ingest")
    def ingest(batch: IngestBatch) -> dict[str, Any]:
        raw = [e.to_raw() for e in batch.events]
        fresh = workspace.live.append_new(raw)
        fresh_ids = {e["event_id"] for e in fresh}
        with workspace.lock:
            try:
                state = workspace.state()
            except HTTPException:
                # 規格暫時是壞的：事件已經保存，規格修好、倉儲重建時會一起載入
                return {"accepted": len(fresh), "duplicates": len(raw) - len(fresh), "warnings": [],
                        "note": "規格目前驗證失敗，事件已保存，規格修好後會出現在報表"}
            # 重建時已經從檔案載入過的不要再插一次
            new_rows = [e for e in fresh if not _exists(state.con, e["event_id"])]
            if new_rows:
                load_events(state.con, new_rows, source="live")
                state._findings = None
            plan = state.project.tracking_plan
        warnings = [
            {"event_id": e.event_id, "event_name": e.event_name, "messages": messages}
            for e in batch.events
            if e.event_id in fresh_ids and (messages := plan_warnings(plan, e))
        ]
        return {"accepted": len(fresh), "duplicates": len(raw) - len(fresh), "warnings": warnings}

    @app.get("/events/live")
    def live_events(limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
        """demo 商店送來的事件，以及 L2 怎麼解析它們（person、session、是否被隔離）。"""
        with workspace.lock:
            state = workspace.state()
            rows = _rows(state.con, f"""
select r.event_id, r.event_name, r.timestamp, r.anonymous_id, r.user_id,
    s.person_id, s.session_id, s.event_id is null as quarantined,
    json(r.properties) as properties
from raw_events r left join stg_events s using (event_id)
where r.source = 'live'
order by r.timestamp desc, r.event_id
limit {limit}""")
        for row in rows:
            row["properties"] = json.loads(row["properties"])
        return rows

    @app.delete("/events/live")
    def clear_live_events() -> dict[str, int]:
        with workspace.lock:
            state = workspace.state()
            (count,) = state.con.execute("select count(*) from raw_events where source = 'live'").fetchone()
            workspace.live.clear()
            state.con.execute("delete from raw_events where source = 'live'")
            state._findings = None
        return {"deleted": count}

    return app


def _exists(con: duckdb.DuckDBPyConnection, event_id: str) -> bool:
    return con.execute("select count(*) from raw_events where event_id = ?", [event_id]).fetchone()[0] > 0


app = create_app()
