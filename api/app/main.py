"""Grainline API。"""

from __future__ import annotations

import duckdb
from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Grainline")

    @app.get("/health")
    def health() -> dict[str, str]:
        duckdb.connect(":memory:").execute("select 1").fetchone()
        return {"status": "ok", "duckdb": "ok"}

    return app


app = create_app()
