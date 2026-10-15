import json

import pytest

from app.hits import decode
from app.models import MODELS, UNATTRIBUTED


def one(con, sql):
    return con.execute(sql).fetchone()[0]


def test_every_model_builds_and_declares_grain(cons) -> None:
    for model in MODELS:
        assert model.grain
        assert one(cons[1], f"select count(*) from {model.name}") >= 0


def test_parsing_keeps_every_hit_and_item(cons, worlds) -> None:
    con, world = cons[1], worlds[1]
    assert one(con, "select count(*) from stg_hits") == len(world.hits)
    expected = sum(len(json.loads(p["items"])) for p in map(decode, (h["url"] for h in world.hits)) if "items" in p)
    assert one(con, "select count(*) from stg_hit_items") == expected
    assert one(con, "select count(*) from stg_hits where ev is null or ts is null or sid is null") == 0


@pytest.mark.parametrize("model", ["last", "first", "linear"])
def test_attribution_adds_up_to_revenue(cons, model) -> None:
    for con in cons.values():
        total = one(con, "select sum(total) from fct_orders")
        attributed = one(con, f"select sum(revenue) from fct_attributed_orders where model = '{model}'")
        assert abs(total - attributed) < 0.01


def test_linear_weights_sum_to_one(cons) -> None:
    bad = one(cons[1], """select count(*) from (select order_id, sum(weight) w from fct_attributed_orders
                          where model = 'linear' group by order_id) where abs(w - 1) > 1e-9""")
    assert bad == 0


def test_missing_order_param_leaves_guest_orders_unattributed(cons) -> None:
    sql = f"select count(*) from fct_attributed_orders where model = 'last' and campaign = '{UNATTRIBUTED}'"
    guests = one(cons[1], "select count(*) from fct_orders where uid is null")
    assert one(cons[1], sql) == guests > 0
    assert one(cons[2], sql) == 0


def test_funnel_units_match_order_lines(cons) -> None:
    assert one(cons[1], "select sum(units) from fct_sku_funnel") == one(cons[1], "select sum(qty) from src_order_lines")
