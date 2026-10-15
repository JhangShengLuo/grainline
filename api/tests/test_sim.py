from collections import Counter, defaultdict

from app.hits import decode
from app.sim import World, simulate
from tests.conftest import SMALL


def _params(world: World) -> list[dict[str, str]]:
    return [decode(h["url"]) for h in world.hits]


def test_deterministic(plans, worlds) -> None:
    again = simulate(plans[1], SMALL)
    assert again.hits == worlds[1].hits and again.orders == worlds[1].orders


def test_plans_change_hits_not_behavior(worlds) -> None:
    v1, v2 = worlds[1], worlds[2]
    assert v1.orders == v2.orders and v1.order_lines == v2.order_lines
    assert len(v1.hits) == len(v2.hits)
    p1 = [p for p in _params(v1) if p["ev"] == "purchase"]
    p2 = [p for p in _params(v2) if p["ev"] == "purchase"]
    assert p1 and all("order" not in p for p in p1)
    assert all("order" in p for p in p2) and len(p2) == len(v2.orders)


def test_multi_touch_guests_and_baskets(worlds) -> None:
    world = worlds[1]
    campaigns = defaultdict(set)
    for p in _params(world):
        if "utm_campaign" in p:
            campaigns[p["aid"]].add(p["utm_campaign"])
    assert any(len(c) >= 2 for c in campaigns.values())
    assert any(o["uid"] is None for o in world.orders)
    lines = Counter(line["order_id"] for line in world.order_lines)
    assert any(n >= 2 for n in lines.values())
    revenue = defaultdict(float)
    for line in world.order_lines:
        revenue[line["order_id"]] += line["line_revenue"]
    assert all(abs(revenue[o["order_id"]] - o["total"]) < 1e-6 for o in world.orders)


def test_hit_ids_follow_time(worlds) -> None:
    hits = worlds[1].hits
    assert [h["hit_id"] for h in hits] == list(range(1, len(hits) + 1))
    assert [h["ts"] for h in hits] == sorted(h["ts"] for h in hits)
