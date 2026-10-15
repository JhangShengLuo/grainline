import json

from app.hits import decode, encode
from app.tracking import load_plans
from tests.conftest import SPECS


def test_round_trip_escapes_values() -> None:
    params = {"ev": "list_view", "page": "/campaign/xmas?a=1&b=2", "list": "聖誕 精選",
              "items": [{"sku": "A01", "cat": "耳機", "price": 1990.0, "pos": 1}], "uid": None}
    url = encode(params)
    assert url.startswith("/collect?ev=list_view&page=")
    assert "uid" not in url and " " not in url and "聖" not in url
    out = decode(url)
    assert out["page"] == "/campaign/xmas?a=1&b=2"
    assert out["list"] == "聖誕 精選"
    assert json.loads(out["items"]) == [{"sku": "A01", "cat": "耳機", "price": 1990.0, "pos": 1}]


def test_numbers_are_plain() -> None:
    assert decode(encode({"price": 1990.0, "qty": 2}))["price"] == "1990"


def test_plans_differ_on_purchase_order() -> None:
    plans = load_plans(SPECS / "tracking")
    assert set(plans) == {1, 2}
    assert not plans[1].has("purchase", "order")
    assert plans[2].has("purchase", "order")
    assert plans[1].has("purchase", "aid")
