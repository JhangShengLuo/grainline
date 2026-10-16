import pytest

from app.journey import journey, sample_people


def test_samples_cover_the_stories(cons) -> None:
    labels = {p["label"] for p in sample_people(cons[2])}
    assert {"跨活動、買了多件", "訪客結帳", "只逛不買"} <= labels


def test_multi_touch_buyer(cons) -> None:
    person = next(p for p in sample_people(cons[2]) if p["label"] == "跨活動、買了多件")
    j = journey(cons[2], person["person_id"])
    assert [s["ts"] for s in j["steps"]] == sorted(s["ts"] for s in j["steps"])
    for step in j["steps"]:
        assert step["url"].startswith("/collect?ev=" + step["ev"])
        items = next((t for t in step["tables"] if t["model"] == "stg_hit_items"), None)
        if step["ev"] == "list_view":
            assert len(items["rows"]) == len(step["page"]["items"]) == 6
    assert j["orders"]
    for order in j["orders"]:
        assert abs(sum(a["weight"] for a in order["attribution"]["linear"]) - 1) < 1e-9
        assert order["after_hit_id"] in {s["hit_id"] for s in j["steps"]}


def test_guest_order_cannot_be_linked_on_v1(cons) -> None:
    guest = next(p for p in sample_people(cons[1]) if p["label"] == "訪客結帳")
    j1 = journey(cons[1], guest["person_id"])
    assert j1["orders"] == []
    assert any(s["ev"] == "purchase" and not s["order_linked"] for s in j1["steps"])
    j2 = journey(cons[2], guest["person_id"])
    assert j2["orders"] and j2["orders"][0]["linked_by"] == "order"


def test_unknown_person(cons) -> None:
    with pytest.raises(KeyError):
        journey(cons[1], "nobody")
