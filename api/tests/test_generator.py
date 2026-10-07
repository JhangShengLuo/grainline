from collections import defaultdict

from app.generator import generate_events
from app.spec import Project

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
        assert set(e["properties"]) == set(declared)
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
    assert timestamps[-1][:10] <= "2026-09-29"


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
