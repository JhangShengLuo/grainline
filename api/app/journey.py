"""單一顧客的流動軌跡：每個 hit → 拆成哪些參數 → 變成哪些中繼表的列 → 訂單怎麼被歸因。"""

from __future__ import annotations

from typing import Any

import duckdb

from .models import BY_NAME
from .questions import _rows


def _clean(row: dict[str, Any]) -> dict[str, Any]:
    return {k: (v.isoformat(sep=" ") if hasattr(v, "isoformat") else v) for k, v in row.items() if v is not None}


def sample_people(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    """挑幾位有代表性的顧客：跨活動又買多件、訪客結帳、只逛不買、一次就買。"""
    picks = [
        ("跨活動、買了多件", """
            select t.person_id from fct_touches t join fct_orders o on o.person_id = t.person_id
            join (select order_id from fct_order_lines group by order_id having count(*) >= 2) l using (order_id)
            group by t.person_id having count(distinct t.campaign) >= 2
            order by count(*) desc, t.person_id limit 1"""),
        ("訪客結帳", """
            select h.aid from stg_hits h join fct_sessions s on s.sid = h.sid and s.utm_campaign is not null
            where h.ev = 'purchase' and h.uid is null group by h.aid order by count(distinct s.sid) desc, h.aid limit 1"""),
        ("只逛不買", """
            select s.person_id from fct_sessions s
            where s.person_id not in (select person_id from fct_orders where person_id is not null)
              and s.person_id not in (select aid from stg_hits where ev = 'purchase')
            group by s.person_id having count(distinct s.utm_campaign) >= 2
            order by count(*) desc, s.person_id limit 1"""),
        ("一次就買", """
            select s.person_id from fct_sessions s join fct_orders o on o.person_id = s.person_id
            group by s.person_id having count(distinct s.sid) = 1 and max(s.utm_campaign) is not null
            order by s.person_id limit 1"""),
    ]
    out, seen = [], set()
    for label, sql in picks:
        row = con.execute(sql).fetchone()
        if row and row[0] not in seen:
            seen.add(row[0])
            out.append(summary(con, row[0]) | {"label": label})
    return out


def _aids(con: duckdb.DuckDBPyConnection, person_id: str) -> list[str]:
    rows = con.execute("select aid from stg_identity where uid = ? union select ?", [person_id, person_id]).fetchall()
    return sorted(r[0] for r in rows)


def summary(con: duckdb.DuckDBPyConnection, person_id: str) -> dict[str, Any]:
    aids = _aids(con, person_id)
    sessions, touches = con.execute(
        "select count(*), count(utm_campaign) from fct_sessions where aid in (select unnest(?))", [aids]).fetchone()
    orders = con.execute("select count(*) from fct_orders where person_id = ?", [person_id]).fetchone()[0]
    return {"person_id": person_id, "sessions": sessions, "touches": touches, "orders": orders}


def _page(hit: dict[str, Any]) -> dict[str, Any]:
    page = hit.get("page") or ""
    if hit["ev"] == "purchase":
        return {"kind": "complete"}
    if page.startswith("/p/"):
        return {"kind": "product", "sku": page[3:]}
    if page == "/checkout":
        return {"kind": "checkout"}
    if page.startswith("/campaign"):
        return {"kind": "campaign"}
    if page.startswith("/c/"):
        return {"kind": "category", "cat": page[3:]}
    if hit["ev"] == "add_to_cart":
        return {"kind": "product", "sku": hit.get("sku")}
    return {"kind": "home"}


def _table(name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    model = BY_NAME[name]
    return {"model": name, "title": model.title, "grain": model.grain, "rows": [_clean(r) for r in rows]}


def journey(con: duckdb.DuckDBPyConnection, person_id: str) -> dict[str, Any]:
    aids = _aids(con, person_id)
    hits = _rows(con, "select * from stg_hits where aid in (select unnest(?)) order by ts, hit_id", [aids])
    if not hits:
        raise KeyError(person_id)
    ids = [h["hit_id"] for h in hits]
    urls = dict(con.execute("select hit_id, url from raw_hits where hit_id in (select unnest(?))", [ids]).fetchall())
    params: dict[int, list[dict[str, Any]]] = {}
    for p in _rows(con, "select hit_id, key, val from stg_hit_params where hit_id in (select unnest(?)) order by hit_id, param_pos", [ids]):
        params.setdefault(p["hit_id"], []).append({"key": p["key"], "value": p["val"]})
    items: dict[int, list[dict[str, Any]]] = {}
    for r in _rows(con, "select * from stg_hit_items where hit_id in (select unnest(?)) order by hit_id, pos", [ids]):
        items.setdefault(r["hit_id"], []).append(r)
    sessions = {r["landing_hit_id"]: r for r in _rows(con, "select * from fct_sessions where aid in (select unnest(?))", [aids])}
    touches = {r["landing_hit_id"]: r for r in _rows(con, "select * from fct_touches where landing_hit_id in (select unnest(?))", [ids])}

    steps = []
    for hit in hits:
        hid = hit["hit_id"]
        tables = [_table("stg_hits", [{k: v for k, v in hit.items() if k not in ("items", "source")}])]
        if hid in items:
            tables.append(_table("stg_hit_items", items[hid]))
        if hid in sessions:
            tables.append(_table("fct_sessions", [sessions[hid]]))
        if hid in touches:
            tables.append(_table("fct_touches", [touches[hid]]))
        steps.append({
            "hit_id": hid, "ts": hit["ts"].isoformat(sep=" "), "sid": hit["sid"], "ev": hit["ev"],
            "url": urls[hid], "params": params.get(hid, []), "page": _page(hit) | ({"items": [_clean(i) for i in items[hid]]} if hid in items else {}),
            "session_start": hid in sessions,
            "campaign": sessions[hid]["utm_campaign"] if hid in sessions else None,
            "tables": tables,
        })

    orders = []
    purchase_hits = [s for s in steps if s["ev"] == "purchase"]
    for o in _rows(con, "select * from fct_orders where person_id = ? order by created_at", [person_id]):
        # 訂單接在哪個 hit 後面：有訂單編號就用它，沒有的話用同一時間的 purchase hit（只是為了畫面擺放）
        after = o["purchase_hit_id"] or next((s["hit_id"] for s in purchase_hits
                                               if s["ts"] == o["created_at"].isoformat(sep=" ")), None)
        lines = _rows(con, "select * from fct_order_lines where order_id = ? order by line_no", [o["order_id"]])
        attribution: dict[str, list[dict[str, Any]]] = {}
        for r in _rows(con, """select a.model, a.campaign, a.touch_id, a.weight, a.revenue from fct_attributed_orders a
                               where order_id = ? order by model, touch_id""", [o["order_id"]]):
            attribution.setdefault(r.pop("model"), []).append(_clean(r))
        orders.append({
            "order_id": o["order_id"], "total": o["total"], "created_at": o["created_at"].isoformat(sep=" "),
            "linked_by": "uid" if o["uid"] else "order" if o["purchase_hit_id"] else None,
            "after_hit_id": after,
            "tables": [_table("fct_orders", [o]), _table("fct_order_lines", lines)],
            "attribution": attribution,
        })
    linked = {o["after_hit_id"] for o in orders}
    for step in purchase_hits:
        step["order_linked"] = step["hit_id"] in linked
    return {"person_id": person_id, "aids": aids, "steps": steps, "orders": orders,
            "touches": [_clean(t) for t in touches.values()]}
