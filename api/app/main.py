"""Grainline API：問題、儀表板、檢查、顧客旅程、定案卡。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse

from . import warehouse
from .checks import fanout, missing
from .decision import decision_card
from .journey import journey, sample_people
from .models import MODELS
from .questions import Question, dashboard, load_questions
from .sim import SimConfig, make_products, simulate
from .tracking import TrackingPlan, load_plans

DEFAULT_SPECS = Path(os.environ.get("GRAINLINE_SPECS", Path(__file__).resolve().parents[2] / "specs" / "flow"))


@dataclass
class State:
    plan: TrackingPlan
    con: duckdb.DuckDBPyConnection


class Workspace:
    def __init__(self, spec_dir: Path, config: SimConfig) -> None:
        self.plans = load_plans(spec_dir / "tracking")
        self.questions = load_questions(spec_dir / "questions.yaml")
        self.config = config
        self.states: dict[int, State] = {}

    def state(self, version: int) -> State:
        if version not in self.plans:
            raise HTTPException(404, f"沒有 v{version} 這個埋點版本")
        if version not in self.states:
            plan = self.plans[version]
            self.states[version] = State(plan, warehouse.build(simulate(plan, self.config)))
        return self.states[version]

    def question(self, qid: str) -> Question:
        if qid not in self.questions:
            raise HTTPException(404, f"沒有 {qid} 這個問題")
        return self.questions[qid]


def create_app(spec_dir: Path = DEFAULT_SPECS, config: SimConfig | None = None) -> FastAPI:
    app = FastAPI(title="Grainline")
    ws = Workspace(spec_dir, config or SimConfig())

    @app.get("/health")
    def health() -> dict[str, str]:
        duckdb.connect(":memory:").execute("select 1").fetchone()
        return {"status": "ok", "duckdb": "ok"}

    @app.get("/plans")
    def plans() -> list[dict[str, Any]]:
        return [p.model_dump() for p in ws.plans.values()]

    @app.get("/products")
    def products() -> list[dict[str, Any]]:
        return [{"sku": p.sku, "name": p.name, "cat": p.cat, "price": p.price} for p in make_products(ws.config.seed)]

    @app.get("/questions")
    def questions() -> list[dict[str, Any]]:
        return [q.model_dump() for q in ws.questions.values()]

    def _attribution(q: Question, attribution: str | None) -> str | None:
        if not q.attribution:
            return None
        if attribution and attribution not in q.attribution.options:
            raise HTTPException(422, f"{q.title} 不支援 {attribution} 歸因")
        return attribution or q.attribution.default

    @app.get("/questions/{qid}")
    def question(qid: str, plan: int = 1, attribution: str | None = None) -> dict[str, Any]:
        q, s = ws.question(qid), ws.state(plan)
        model = _attribution(q, attribution)
        cur = s.con.cursor()
        return {
            "question": q.model_dump(),
            "plan": plan,
            "attribution": model,
            "dashboard": dashboard(cur, q, model),
            "fanout": fanout(cur, q),
            "missing": missing(cur, s.plan, q),
        }

    @app.get("/questions/{qid}/decision", response_class=PlainTextResponse)
    def decision(qid: str, plan: int = 1, attribution: str | None = None) -> str:
        q, s = ws.question(qid), ws.state(plan)
        cur = s.con.cursor()
        return decision_card(q, s.plan, _attribution(q, attribution), fanout(cur, q), missing(cur, s.plan, q))

    @app.get("/journeys")
    def journeys(plan: int = 1) -> list[dict[str, Any]]:
        return sample_people(ws.state(plan).con.cursor())

    @app.get("/journeys/{person_id}")
    def person(person_id: str, plan: int = 1) -> dict[str, Any]:
        try:
            return journey(ws.state(plan).con.cursor(), person_id)
        except KeyError:
            raise HTTPException(404, f"找不到 {person_id}") from None

    @app.get("/models")
    def models(plan: int = Query(1)) -> list[dict[str, Any]]:
        cur = ws.state(plan).con.cursor()
        return [{"name": m.name, "layer": m.layer, "title": m.title, "grain": m.grain, "upstream": list(m.upstream),
                 "rows": cur.execute(f"select count(*) from {m.name}").fetchone()[0]} for m in MODELS]

    return app


app = create_app()
