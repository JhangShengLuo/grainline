"""三個行銷問題：顧問立場、需要的埋點，以及標準儀表板。"""

from __future__ import annotations

from pathlib import Path
from statistics import median, quantiles
from typing import Any

import duckdb
import yaml
from pydantic import BaseModel

from .models import ATTRIBUTION_MODELS, DIRECT, UNATTRIBUTED

PSEUDO = (DIRECT, UNATTRIBUTED)


class Requirement(BaseModel):
    event: str
    param: str
    why: str


class Attribution(BaseModel):
    default: str
    options: list[str]


class Question(BaseModel):
    id: str = ""
    title: str
    ask: str
    phrasings: list[str]
    stance: str
    grain: str
    attribution: Attribution | None = None
    fanout: str
    requires: list[Requirement]


def load_questions(path: Path) -> dict[str, Question]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {qid: Question.model_validate(body | {"id": qid}) for qid, body in raw.items()}


def _rows(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None) -> list[dict[str, Any]]:
    cursor = con.execute(sql, params or [])
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _order(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    """活動依數字排序，(直接進站)、(無法歸因) 放最後。"""
    return sorted(rows, key=lambda r: (r["campaign"] in PSEUDO, PSEUDO.index(r["campaign"]) if r["campaign"] in PSEUDO else 0, -r[key]))


def budget(con: duckdb.DuckDBPyConnection, attribution: str) -> dict[str, Any]:
    by_model = _rows(con, """
        select campaign, model, sum(revenue) as revenue, sum(weight) as orders
        from fct_attributed_orders group by all""")
    total = con.execute("select sum(total) from fct_orders").fetchone()[0] or 0.0
    campaigns: dict[str, dict[str, Any]] = {}
    for r in by_model:
        c = campaigns.setdefault(r["campaign"], {"campaign": r["campaign"]} | {m: 0.0 for m in ATTRIBUTION_MODELS})
        c[r["model"]] = r["revenue"]
        if r["model"] == attribution:
            c["revenue"], c["orders"] = r["revenue"], r["orders"]
    rows = [c | {"share": c.get("revenue", 0.0) / total if total else 0.0} for c in campaigns.values()]
    for row in rows:
        row.setdefault("revenue", 0.0)
        row.setdefault("orders", 0.0)
    return {"total": total, "rows": _order(rows, "revenue")}


def assists(con: duckdb.DuckDBPyConnection, attribution: str) -> dict[str, Any]:
    rows = _rows(con, """
        with touched as (select campaign, count(distinct order_id) as touched_orders, sum(revenue) as linear_revenue
                         from fct_attributed_orders where model = 'linear' group by campaign),
             last as (select campaign, sum(weight) as last_orders, sum(revenue) as last_revenue
                      from fct_attributed_orders where model = 'last' group by campaign)
        select campaign, coalesce(touched_orders, 0) as touched_orders, coalesce(last_orders, 0) as last_orders,
               coalesce(touched_orders, 0) - coalesce(last_orders, 0) as assist_orders,
               coalesce(linear_revenue, 0) as linear_revenue, coalesce(last_revenue, 0) as last_revenue
        from touched full join last using (campaign)""")
    return {"rows": _order([r for r in rows if r["campaign"] not in PSEUDO], "touched_orders")}


def category(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    rows = _rows(con, "select * from fct_sku_funnel order by views desc")
    for r in rows:
        r["view_to_order"] = r["orders"] / r["views"] if r["views"] else 0.0
    views = [r["views"] for r in rows]
    rates = [r["view_to_order"] for r in rows if r["views"]]
    low = quantiles(rates, n=4)[0] if len(rates) >= 4 else 0.0
    for r in rows:
        r["flag"] = r["views"] >= median(views) and r["view_to_order"] <= low
    cats: dict[str, dict[str, Any]] = {}
    for r in rows:
        c = cats.setdefault(r["cat"], {"cat": r["cat"], "impressions": 0, "views": 0, "adds": 0, "orders": 0, "units": 0, "revenue": 0.0})
        for key in ("impressions", "views", "adds", "orders", "units", "revenue"):
            c[key] += r[key]
    return {"rows": rows, "categories": sorted(cats.values(), key=lambda c: -c["revenue"])}


def dashboard(con: duckdb.DuckDBPyConnection, question: Question, attribution: str | None = None) -> dict[str, Any]:
    if question.id == "budget":
        return budget(con, attribution or "last")
    if question.id == "assists":
        return assists(con, attribution or "linear")
    return category(con)
