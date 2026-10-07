from collections import Counter, defaultdict

from app.generator import generate_events
from app.spec import Project, load_project

from .conftest import SHOP_SPECS

PY_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool}


def test_same_seed_same_events(project: Project) -> None:
    assert generate_events(project) == generate_events(project)


def test_different_seed_different_events(project: Project) -> None:
    other = project.model_copy(
        update={"simulation": project.simulation.model_copy(update={"seed": 7})}
    )
    assert generate_events(project) != generate_events(other)


def test_every_event_conforms_to_tracking_plan(project: Project) -> None:
    plan = project.tracking_plan
    for e in generate_events(project):
        declared = plan.properties_of(e["event_name"])
        # 情境裡沒有值的 property 不帶（例如沒用折扣碼），其他都要有
        assert set(e["properties"]) <= set(declared)
        assert {n for n, p in declared.items() if p.required} <= set(e["properties"])
        for name, value in e["properties"].items():
            prop = declared[name]
            assert isinstance(value, PY_TYPES[prop.type]), (e["event_name"], name, value)
            if prop.enum:
                assert value in prop.enum
            if prop.min is not None:
                assert value >= prop.min


def test_events_cover_whole_period_in_order(project: Project) -> None:
    events = generate_events(project)
    timestamps = [e["timestamp"] for e in events]
    assert timestamps == sorted(timestamps)
    assert timestamps[0].startswith("2026-09-01")
    assert timestamps[-1] < "2026-09-29"  # 觀察期在 9/28 午夜結束


def test_order_revenue_matches_checkout_cart_value(project: Project) -> None:
    last_cart_value: dict[str, float] = {}
    orders = 0
    for e in generate_events(project):
        props = e["properties"]
        if e["event_name"] == "checkout_start":
            last_cart_value[e["anonymous_id"]] = props["cart_value"]
        elif e["event_name"] == "order_completed":
            assert props["revenue"] == last_cart_value[e["anonymous_id"]]
            assert props["revenue"] > 0
            orders += 1
    assert orders > 0


def test_v2_optional_properties_follow_context() -> None:
    v2 = load_project(SHOP_SPECS, 2)
    for e in generate_events(v2):
        if e["event_name"] == "page_view":
            assert ("product_id" in e["properties"]) == (e["properties"]["page_type"] == "product")


def test_v2_records_the_same_behavior_differently(project: Project) -> None:
    v2 = load_project(SHOP_SPECS, 2)
    counts = {
        version: Counter(e["event_name"] for e in generate_events(p))
        for version, p in ((1, project), (2, v2))
    }
    # 同樣的人、同樣的行為：沒改到的事件數量完全相同
    for name in ("page_view", "checkout_start", "login"):
        assert counts[1][name] == counts[2][name]
    # 點擊就送：API 失敗的點擊也被記錄
    assert counts[2]["add_to_cart"] > counts[1]["add_to_cart"]
    assert "product_view" not in counts[2]
    orders = [e["properties"]["order_id"] for e in generate_events(v2) if e["event_name"] == "order_completed"]
    assert len(orders) > len(set(orders))  # 成功頁重新整理造成重複
    coupons = [e for e in generate_events(v2) if e["event_name"] == "order_completed" and "coupon_code" in e["properties"]]
    assert 0 < len(coupons) < len(orders)


def test_user_id_appears_only_after_login_on_that_device(project: Project) -> None:
    logged_in: set[str] = set()
    by_user: dict[str, set[str]] = defaultdict(set)
    for e in generate_events(project):
        if e["event_name"] == "login":
            logged_in.add(e["anonymous_id"])
        if e["user_id"] is not None:
            assert e["anonymous_id"] in logged_in
            by_user[e["user_id"]].add(e["anonymous_id"])
    # 有人在 web 和 app 都登入過，身分合併才有東西可以合
    assert any(len(devices) > 1 for devices in by_user.values())
