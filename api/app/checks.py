"""兩種最常吵的問題，用假資料上的數字證明：會不會乘開、埋點缺不缺欄位。"""

from __future__ import annotations

from typing import Any

import duckdb

from .questions import Question, _rows
from .tracking import TrackingPlan

# 每種錯誤 join：錯的 SQL、對的 SQL、各活動（或 SKU）錯的數字、一個範例
FANOUTS: dict[str, dict[str, str]] = {
    "lines_x_touches": {
        "label": "訂單明細直接 join 活動觸點，再算訂單數和營收",
        "wrong_sql": "select t.campaign, count(*) as orders, sum(o.total) as revenue\n"
                     "from fct_order_lines l join fct_orders o using (order_id)\n"
                     "join fct_touches t on t.person_id = o.person_id\ngroup by t.campaign",
        "right_sql": "select campaign, sum(weight) as orders, sum(revenue) as revenue\n"
                     "from fct_attributed_orders where model = 'last' group by campaign",
        "per_key": """select t.campaign as key, count(*) as orders, sum(o.total) as revenue
                      from fct_order_lines l join fct_orders o using (order_id)
                      join fct_touches t on t.person_id = o.person_id group by t.campaign""",
        "totals": """select count(*) as orders, sum(o.total) as revenue
                     from fct_order_lines l join fct_orders o using (order_id)
                     join fct_touches t on t.person_id = o.person_id""",
        "right_totals": """select count(*) as orders, sum(total) as revenue from fct_orders
                           where order_id in (select o.order_id from fct_orders o
                                              join fct_touches t on t.person_id = o.person_id)""",
        "example": """select o.order_id as key, count(distinct l.line_no) as lines, count(distinct t.touch_id) as touches,
                             count(*) as rows_after
                      from fct_order_lines l join fct_orders o using (order_id)
                      join fct_touches t on t.person_id = o.person_id
                      group by o.order_id having count(distinct l.line_no) >= 2 and count(distinct t.touch_id) >= 2
                      order by rows_after desc, key limit 1""",
    },
    "touches_x_orders": {
        "label": "每個活動觸點都拿走整張訂單的金額",
        "wrong_sql": "select t.campaign, count(*) as orders, sum(o.total) as revenue\n"
                     "from fct_orders o join fct_touches t on t.person_id = o.person_id\ngroup by t.campaign",
        "right_sql": "select campaign, sum(weight) as orders, sum(revenue) as revenue\n"
                     "from fct_attributed_orders where model = 'linear' group by campaign",
        "per_key": """select t.campaign as key, count(*) as orders, sum(o.total) as revenue
                      from fct_orders o join fct_touches t on t.person_id = o.person_id group by t.campaign""",
        "totals": """select count(*) as orders, sum(o.total) as revenue
                     from fct_orders o join fct_touches t on t.person_id = o.person_id""",
        "right_totals": """select count(*) as orders, sum(total) as revenue from fct_orders
                           where order_id in (select o.order_id from fct_orders o
                                              join fct_touches t on t.person_id = o.person_id)""",
        "example": """select o.order_id as key, 1 as lines, count(*) as touches, count(*) as rows_after
                      from fct_orders o join fct_touches t on t.person_id = o.person_id
                      group by o.order_id having count(*) >= 2 order by rows_after desc, key limit 1""",
    },
    "items_x_lines": {
        "label": "列表曝光的商品直接 join 訂單明細，再算件數",
        "wrong_sql": "select i.sku, sum(l.qty) as units\nfrom stg_hit_items i join fct_order_lines l using (sku)\ngroup by i.sku",
        "right_sql": "select sku, units from fct_sku_funnel",
        "per_key": """select i.sku as key, sum(l.qty) as units
                      from stg_hit_items i join fct_order_lines l using (sku) group by i.sku""",
        "totals": "select sum(l.qty) as units from stg_hit_items i join fct_order_lines l using (sku)",
        "right_totals": "select sum(qty) as units from fct_order_lines",
        "example": """select l.order_id || ' / ' || l.sku as key, 1 as lines, count(*) as touches, count(*) as rows_after
                      from stg_hit_items i join fct_order_lines l using (sku)
                      group by l.order_id, l.sku order by rows_after desc, key limit 1""",
    },
}


def fanout(con: duckdb.DuckDBPyConnection, question: Question) -> dict[str, Any]:
    spec = FANOUTS[question.fanout]
    wrong = _rows(con, spec["totals"])[0]
    right = _rows(con, spec["right_totals"])[0]
    metric = "units" if "units" in wrong else "orders"
    ratio = (wrong[metric] or 0) / right[metric] if right[metric] else 0.0
    example = _rows(con, spec["example"])
    return {
        "kind": question.fanout,
        "label": spec["label"],
        "wrong_sql": spec["wrong_sql"],
        "right_sql": spec["right_sql"],
        "wrong": wrong,
        "right": right,
        "metric": metric,
        "ratio": ratio,
        "per_key": {r["key"]: r for r in _rows(con, spec["per_key"])},
        "example": example[0] if example else None,
    }


def missing(con: duckdb.DuckDBPyConnection, plan: TrackingPlan, question: Question) -> list[dict[str, Any]]:
    out = []
    for req in question.requires:
        hits, present = con.execute(f"""
            select count(*), count(*) filter (where p.hit_id is not null)
            from stg_hits h left join (select distinct hit_id from stg_hit_params where key = ?) p using (hit_id)
            where h.ev = ?""", [req.param, req.event]).fetchone()
        rate = present / hits if hits else 0.0
        status = "ok" if rate >= 0.99 else "missing" if present == 0 else "partial"
        item = {"event": req.event, "param": req.param, "why": req.why, "hits": hits, "present": present,
                "rate": rate, "status": status, "in_plan": plan.has(req.event, req.param), "impact": None}
        if (req.event, req.param) == ("purchase", "order"):
            lost, total = con.execute("""
                select coalesce(sum(total) filter (where person_id is null), 0), sum(total) from fct_orders""").fetchone()
            item["impact"] = {"label": "無法歸因的銷售額", "value": lost, "share": lost / total if total else 0.0}
        out.append(item)
    return out
