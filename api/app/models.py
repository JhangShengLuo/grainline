"""分層 view：原始 hit → 解析（staging）→ 中繼表（mart）。每張表都宣告粒度（1 列代表什麼）和上游。

全部是 view（schema-on-read）：原始資料不可變，換埋點規格或加進即時的 hit，下游馬上跟著變。
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb

ITEM_TYPE = '[{"sku":"VARCHAR","cat":"VARCHAR","price":"DOUBLE","pos":"INTEGER"}]'
ATTRIBUTION_MODELS = ("last", "first", "linear")
LOOKBACK_DAYS = 30
UNATTRIBUTED = "(無法歸因)"
DIRECT = "(直接進站)"


@dataclass(frozen=True)
class Model:
    name: str
    layer: str           # raw | staging | mart
    title: str           # 短標籤
    grain: str           # 1 列 = ?
    upstream: tuple[str, ...]
    sql: str = ""        # raw 表沒有 SQL


def _param(key: str, cast: str | None = None, alias: str | None = None) -> str:
    expr = f"max(val) filter (where key = '{key}')"
    if cast:
        expr = f"try_cast({expr} as {cast})"
    return f"{expr} as {alias or key}"


MODELS: list[Model] = [
    Model("raw_hits", "raw", "前端送來的原始 hit", "1 個 hit（一串 URL）", ()),
    Model("src_orders", "raw", "後端訂單主檔", "1 張訂單", ()),
    Model("src_order_lines", "raw", "後端訂單明細", "1 張訂單的 1 個 SKU", ()),
    Model("src_products", "raw", "商品主檔", "1 個 SKU", ()),
    Model(
        "stg_hit_params", "staging", "拆開 URL 參數", "1 個 hit 的 1 個參數", ("raw_hits",),
        """
        select h.hit_id, h.source, k.i as param_pos,
               split_part(h.kvs[k.i], '=', 1) as key,
               url_decode(split_part(h.kvs[k.i], '=', 2)) as val
        from (select hit_id, source, string_split(split_part(url, '?', 2), '&') as kvs from raw_hits) h,
             unnest(range(1, len(h.kvs) + 1)) as k(i)
        """,
    ),
    Model(
        "stg_hits", "staging", "每個 hit 一列", "1 個 hit", ("stg_hit_params",),
        "select hit_id, any_value(source) as source, "
        + ", ".join([
            _param("ev"), _param("aid"), _param("uid"), _param("sid"), _param("ts", "timestamp"),
            _param("page"), _param("utm_source"), _param("utm_medium"), _param("utm_campaign"),
            _param("list"), _param("items"), _param("sku"), _param("cat"), _param("price", "double"),
            _param("qty", "integer"), _param("order", alias="order_id"), _param("value", "double"),
        ])
        + " from stg_hit_params group by hit_id",
    ),
    Model(
        "stg_hit_items", "staging", "展開 items 陣列", "1 個 hit 裡的 1 個商品", ("stg_hits",),
        f"""
        select h.hit_id, h.ts, h.sid, h.aid, u.it.pos as pos, u.it.sku as sku, u.it.cat as cat, u.it.price as price
        from stg_hits h, unnest(from_json(h.items, '{ITEM_TYPE}')) as u(it)
        where h.items is not null
        """,
    ),
    Model(
        "stg_identity", "staging", "瀏覽器 → 會員", "1 個瀏覽器（aid）", ("stg_hits",),
        "select aid, min(uid) as uid from stg_hits where uid is not null group by aid",
    ),
    Model(
        "fct_sessions", "mart", "造訪", "1 次造訪（session）", ("stg_hits", "stg_identity"),
        """
        with s as (select sid, min(hit_id) as landing_hit_id, min(ts) as start_ts, max(ts) as end_ts,
                          count(*) as hits from stg_hits group by sid)
        select s.sid, h.aid, coalesce(i.uid, h.aid) as person_id, s.start_ts, s.end_ts, s.hits,
               h.utm_source, h.utm_medium, h.utm_campaign, s.landing_hit_id
        from s join stg_hits h on h.hit_id = s.landing_hit_id
        left join stg_identity i on i.aid = h.aid
        """,
    ),
    Model(
        "fct_touches", "mart", "活動觸點", "1 次從活動進站", ("fct_sessions",),
        """
        select sid as touch_id, person_id, start_ts as touched_at, utm_source, utm_medium,
               utm_campaign as campaign, landing_hit_id
        from fct_sessions where utm_campaign is not null
        """,
    ),
    Model(
        "fct_orders", "mart", "訂單", "1 張訂單", ("src_orders", "stg_hits", "stg_identity"),
        """
        select o.order_id, o.uid, o.created_at, o.total, o.item_count,
               ph.hit_id as purchase_hit_id, ph.aid as purchase_aid,
               coalesce(o.uid, i.uid, ph.aid) as person_id
        from src_orders o
        left join stg_hits ph on ph.ev = 'purchase' and ph.order_id = o.order_id
        left join stg_identity i on i.aid = ph.aid
        """,
    ),
    Model(
        "fct_order_lines", "mart", "訂單明細", "1 張訂單的 1 個 SKU", ("src_order_lines", "src_products"),
        """
        select l.order_id, l.line_no, l.sku, p.name, p.cat, l.qty, l.unit_price, l.line_revenue
        from src_order_lines l join src_products p using (sku)
        """,
    ),
    Model(
        "fct_attributed_orders", "mart", "歸因後的訂單", "1 張訂單 × 1 個觸點 × 1 種歸因方式",
        ("fct_orders", "fct_touches"),
        f"""
        with t as (
            select o.order_id, o.total, tc.campaign, tc.touch_id,
                   row_number() over (partition by o.order_id order by tc.touched_at desc, tc.touch_id) as rn_last,
                   row_number() over (partition by o.order_id order by tc.touched_at, tc.touch_id) as rn_first,
                   count(*) over (partition by o.order_id) as n
            from fct_orders o
            join fct_touches tc on tc.person_id = o.person_id and tc.touched_at <= o.created_at
                 and tc.touched_at >= o.created_at - interval {LOOKBACK_DAYS} day
        ),
        none as (
            select o.order_id, o.total,
                   case when o.person_id is null then '{UNATTRIBUTED}' else '{DIRECT}' end as campaign
            from fct_orders o where o.order_id not in (select order_id from t)
        )
        select order_id, 'last' as model, campaign, touch_id, 1.0 as weight, total as revenue from t where rn_last = 1
        union all
        select order_id, 'first', campaign, touch_id, 1.0, total from t where rn_first = 1
        union all
        select order_id, 'linear', campaign, touch_id, 1.0 / n, total / n from t
        union all
        select order_id, m.model, campaign, null, 1.0, total
        from none, (values ('last'), ('first'), ('linear')) as m(model)
        """,
    ),
    Model(
        "fct_sku_funnel", "mart", "SKU 漏斗", "1 個 SKU", ("src_products", "stg_hit_items", "stg_hits", "fct_order_lines"),
        """
        select p.sku, p.name, p.cat, p.price,
               coalesce(i.n, 0) as impressions, coalesce(v.n, 0) as views, coalesce(a.n, 0) as adds,
               coalesce(l.orders, 0) as orders, coalesce(l.units, 0) as units, coalesce(l.revenue, 0) as revenue
        from src_products p
        left join (select sku, count(*) as n from stg_hit_items group by sku) i using (sku)
        left join (select sku, count(*) as n from stg_hits where ev = 'product_view' group by sku) v using (sku)
        left join (select sku, count(*) as n from stg_hits where ev = 'add_to_cart' group by sku) a using (sku)
        left join (select sku, count(distinct order_id) as orders, sum(qty) as units, sum(line_revenue) as revenue
                   from fct_order_lines group by sku) l using (sku)
        """,
    ),
]
BY_NAME = {m.name: m for m in MODELS}


def apply(con: duckdb.DuckDBPyConnection) -> None:
    for model in MODELS:
        if model.sql:
            con.execute(f"create or replace view {model.name} as {model.sql}")
