"""Grainline API：相容性檢查、數字證據、血緣、報表、tracking plan 版本比較，以及 demo 商店的 ingest。

每個 tracking plan 版本各有一個倉儲：同一個 seed 的模擬使用者，加上 demo 商店在那個版本下送出的事件。
倉儲在第一次用到時才建立（約 1 秒）；規格檔有任何變動，下一個請求會全部重建。
所有端點都接受 ?plan=<version>，省略時用 status: current 的版本。
"""

from __future__ import annotations

import json
import os
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import yaml
from fastapi import FastAPI, HTTPException, Query
from pydantic import ValidationError

from .catalog import catalog_items
from .checks import Finding, check_project, effective_additivity, metric_format
from .diff import compare
from .generator import generate_events
from .ingest import IngestBatch, IncomingEvent, LiveStore, plan_warnings
from .lineage import Lineage, metric_lineage, resolve
from .spec import Project, default_plan_version, load_project, read_tracking_plans
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


def _spec_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ValidationError):
        errors = [str(e["msg"]) for e in exc.errors()]
    else:
        errors = [str(exc)]
    return HTTPException(422, {"message": "規格驗證失敗", "errors": errors})


class Workspace:
    """所有呼叫端都必須持有 self.lock。"""

    def __init__(self, spec_dir: Path, data_dir: Path) -> None:
        self.spec_dir = spec_dir
        self.live = LiveStore(data_dir / f"live_events_{spec_dir.name}.ndjson")
        self.lock = threading.Lock()
        self._fingerprint: tuple[Any, ...] | None = None
        # (版本, 是否包含 demo 商店的事件) → 倉儲
        self._states: dict[tuple[int, bool], State] = {}

    def _refresh(self) -> None:
        fingerprint = tuple(
            (str(p.relative_to(self.spec_dir)), p.stat().st_mtime_ns, p.stat().st_size)
            for p in sorted(self.spec_dir.rglob("*.yaml"))
        )
        if fingerprint != self._fingerprint:
            for state in self._states.values():
                state.con.close()
            self._states = {}
            self._fingerprint = fingerprint

    def plans(self) -> dict[int, dict[str, Any]]:
        self._refresh()
        try:
            return read_tracking_plans(self.spec_dir)
        except (ValueError, yaml.YAMLError) as exc:
            raise _spec_error(exc) from exc

    def resolve_version(self, version: int | None) -> int:
        plans = self.plans()
        if version is None:
            return default_plan_version(plans)
        if version not in plans:
            raise HTTPException(404, f"沒有 tracking plan v{version}（有：{', '.join(f'v{v}' for v in plans)}）")
        return version

    def state(self, version: int | None = None, include_live: bool = True) -> State:
        """include_live=False：只有模擬資料，版本比較用（兩個版本是同一群人、同樣的行為）。"""
        version = self.resolve_version(version)
        key = (version, include_live)
        if key not in self._states:
            try:
                project = load_project(self.spec_dir, version)
            except (ValidationError, ValueError, yaml.YAMLError) as exc:
                raise _spec_error(exc) from exc
            lineage = resolve(project)
            con = duckdb.connect(":memory:")
            events = generate_events(project)
            build_warehouse(con, project, events, lineage)
            if include_live:
                live = [e for e in self.live.read_all() if e["tracking_plan_version"] == version]
                load_events(con, live, source="live")
            self._states[key] = State(project, lineage, con, len(events))
        return self._states[key]

    def built(self, version: int) -> State | None:
        return self._states.get((version, True))


def _rows(con: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    cursor = con.execute(sql, params or [])
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def _exists(con: duckdb.DuckDBPyConnection, event_id: str) -> bool:
    return con.execute("select count(*) from raw_events where event_id = ?", [event_id]).fetchone()[0] > 0


def create_app(spec_dir: Path | None = None, data_dir: Path | None = None) -> FastAPI:
    workspace = Workspace(
        spec_dir or Path(os.environ.get("GRAINLINE_SPECS", DEFAULT_SPECS)),
        data_dir or Path(os.environ.get("GRAINLINE_DATA", DEFAULT_DATA)),
    )
    app = FastAPI(title="Grainline API")
    PlanParam = Query(None, description="tracking plan 版本；省略時用 status: current 的版本")

    @app.get("/health")
    def health() -> dict[str, str]:
        with duckdb.connect(":memory:") as con:
            con.execute("select 1").fetchone()
        return {"status": "ok", "duckdb": "ok"}

    # ---------- tracking plan ----------

    @app.get("/tracking-plans")
    def tracking_plans() -> list[dict[str, Any]]:
        with workspace.lock:
            plans = workspace.plans()
            default = default_plan_version(plans)
        return [
            {
                "version": v,
                "name": raw.get("name", ""),
                "status": raw.get("status", "proposal"),
                "description": raw.get("description", ""),
                "default": v == default,
            }
            for v, raw in plans.items()
        ]

    @app.get("/tracking-plan")
    def tracking_plan(plan: int | None = PlanParam) -> dict[str, Any]:
        with workspace.lock:
            state = workspace.state(plan)
        return state.project.tracking_plan.model_dump(exclude_none=True, by_alias=True)

    @app.get("/diff")
    def diff(base: int | None = Query(None), target: int | None = Query(None)) -> dict[str, Any]:
        """預設比較 current 版本與最新的另一個版本。只用模擬資料：同一群人、同樣的行為，差異全部來自埋點設計。"""
        with workspace.lock:
            plans = workspace.plans()
            base_v = workspace.resolve_version(base)
            if target is None:
                others = [v for v in plans if v != base_v]
                if not others:
                    raise HTTPException(404, "只有一個 tracking plan 版本，沒有東西可以比較")
                target = max(others)
            target_v = workspace.resolve_version(target)
            return compare(workspace.state(base_v, include_live=False), workspace.state(target_v, include_live=False))

    # ---------- 檢查、指標、報表 ----------

    @app.get("/checks")
    def checks(rule: str | None = Query(None, pattern="^R[123]$"), plan: int | None = PlanParam) -> dict[str, Any]:
        with workspace.lock:
            state = workspace.state(plan)
            findings = [f.to_dict() for f in state.findings() if rule is None or f.rule == rule]
        return {
            "project": state.project.name,
            "tracking_plan_version": state.project.tracking_plan.version,
            "built_at": state.built_at.isoformat(timespec="seconds"),
            "generated_events": state.events,
            "summary": {
                "error": sum(f["severity"] == "error" for f in findings),
                "warning": sum(f["severity"] == "warning" for f in findings),
            },
            "findings": findings,
        }

    @app.get("/metrics")
    def metrics(plan: int | None = PlanParam) -> list[dict[str, Any]]:
        with workspace.lock:
            state = workspace.state(plan)
        project = state.project
        return [
            {
                "name": name,
                "label": metric.label,
                "description": metric.description,
                "type": metric.type,
                "declared_additivity": getattr(metric, "additivity", None),
                "additivity": effective_additivity(project, name),
                "format": metric_format(project, name),
                "broken": state.lineage.broken_metrics.get(name),
            }
            for name, metric in project.metric_layer.metrics.items()
        ]

    @app.get("/metrics/{name}/lineage")
    def lineage(name: str, plan: int | None = PlanParam) -> dict[str, Any]:
        with workspace.lock:
            state = workspace.state(plan)
        if name not in state.project.metric_layer.metrics:
            raise HTTPException(404, f"沒有指標 {name}")
        return metric_lineage(state.project, state.lineage, name)

    @app.get("/reports")
    def reports(plan: int | None = PlanParam) -> list[dict[str, Any]]:
        with workspace.lock:
            state = workspace.state(plan)
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
    def report(name: str, plan: int | None = PlanParam) -> dict[str, Any]:
        with workspace.lock:
            state = workspace.state(plan)
            spec = state.project.metric_layer.reports.get(name)
            if spec is None:
                raise HTTPException(404, f"沒有報表 {name}")
            if name in state.lineage.broken_reports:
                raise HTTPException(409, {"message": "報表血緣斷掉，無法建立", "reason": state.lineage.broken_reports[name]})
            rows = _rows(state.con, f"select * from rpt_{name}")
            live_periods = [
                r["period"] for r in _rows(state.con, f"""
select distinct cast(date_trunc('{spec.time_grain}', timestamp) as date) as period
from raw_events where source = 'live' order by 1""")
            ]
        metrics = state.project.metric_layer.metrics
        return {
            "name": name,
            "label": spec.label,
            "tracking_plan_version": state.project.tracking_plan.version,
            "time_grain": spec.time_grain,
            "dimensions": spec.dimensions,
            "metrics": [
                {
                    "name": m,
                    "label": metrics[m].label,
                    "additivity": effective_additivity(state.project, m),
                    "format": metric_format(state.project, m),
                }
                for m in spec.metrics
            ],
            "live_periods": live_periods,
            "rows": rows,
        }

    # ---------- demo 商店 ----------

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
        warnings: list[dict[str, Any]] = []
        by_version: dict[int, list[IncomingEvent]] = defaultdict(list)
        for event in batch.events:
            if event.event_id in fresh_ids:
                by_version[event.tracking_plan_version].append(event)
        with workspace.lock:
            try:
                plans = workspace.plans()
            except HTTPException:
                # 規格暫時是壞的：事件已經保存，規格修好、倉儲重建時會一起載入
                return {"accepted": len(fresh), "duplicates": len(raw) - len(fresh), "warnings": [],
                        "note": "規格目前驗證失敗，事件已保存，規格修好後會出現在報表"}
            for version, events in by_version.items():
                if version not in plans:
                    warnings += [
                        {"event_id": e.event_id, "event_name": e.event_name,
                         "messages": [f"tracking plan v{version} 不存在：事件已保存，但不會出現在任何報表"]}
                        for e in events
                    ]
                    continue
                state = workspace.state(version)
                # 剛建立的倉儲已經從檔案載入過，不要再插一次
                new_rows = [e.to_raw() for e in events if not _exists(state.con, e.event_id)]
                if new_rows:
                    load_events(state.con, new_rows, source="live")
                    state._findings = None
                plan = state.project.tracking_plan
                warnings += [
                    {"event_id": e.event_id, "event_name": e.event_name, "messages": messages}
                    for e in events
                    if (messages := plan_warnings(plan, e))
                ]
        return {"accepted": len(fresh), "duplicates": len(raw) - len(fresh), "warnings": warnings}

    @app.get("/events/live")
    def live_events(limit: int = Query(50, ge=1, le=500), plan: int | None = PlanParam) -> list[dict[str, Any]]:
        """demo 商店在這個版本下送來的事件，以及 L2 怎麼解析它們（person、session、是否被隔離）。"""
        with workspace.lock:
            state = workspace.state(plan)
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
        """刪除 demo 商店送出的所有事件（所有版本）；模擬資料不受影響。"""
        with workspace.lock:
            deleted = len(workspace.live.read_all())
            workspace.live.clear()
            for version in list(workspace.plans()):
                if state := workspace.built(version):
                    state.con.execute("delete from raw_events where source = 'live'")
                    state._findings = None
        return {"deleted": deleted}

    return app


app = create_app()
