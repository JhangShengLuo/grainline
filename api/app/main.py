import duckdb
from fastapi import FastAPI

app = FastAPI(title="Grainline API")


@app.get("/health")
def health() -> dict[str, str]:
    with duckdb.connect(":memory:") as con:
        con.execute("select 1").fetchone()
    return {"status": "ok", "duckdb": "ok"}
