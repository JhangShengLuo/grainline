import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import SMALL, SPECS


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(create_app(SPECS, SMALL))


def test_health(client) -> None:
    assert client.get("/health").json() == {"status": "ok", "duckdb": "ok"}


def test_catalogs(client) -> None:
    assert [p["version"] for p in client.get("/plans").json()] == [1, 2]
    assert [q["id"] for q in client.get("/questions").json()] == ["budget", "assists", "category"]
    assert len(client.get("/products").json()) == 24
    models = client.get("/models").json()
    assert all(m["grain"] for m in models) and any(m["name"] == "fct_attributed_orders" for m in models)


def test_question(client) -> None:
    body = client.get("/questions/budget", params={"plan": 1, "attribution": "first"}).json()
    assert body["attribution"] == "first"
    assert body["dashboard"]["rows"] and body["fanout"]["ratio"] > 1
    assert any(m["status"] == "missing" for m in body["missing"])
    assert client.get("/questions/category").json()["attribution"] is None


def test_decision_is_markdown(client) -> None:
    response = client.get("/questions/budget/decision", params={"plan": 1})
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text.startswith("# 定案：預算分配")


def test_journeys(client) -> None:
    people = client.get("/journeys", params={"plan": 2}).json()
    j = client.get(f"/journeys/{people[0]['person_id']}", params={"plan": 2}).json()
    assert j["steps"] and j["steps"][0]["tables"][0]["model"] == "stg_hits"


@pytest.mark.parametrize("path", ["/questions/nope", "/questions/budget?plan=9", "/journeys/nobody",
                                  "/questions/category/decision?plan=3"])
def test_not_found(client, path) -> None:
    assert client.get(path).status_code == 404


def test_bad_attribution(client) -> None:
    assert client.get("/questions/budget", params={"attribution": "magic"}).status_code == 422


def test_live_journey(client) -> None:
    client.delete("/live")
    assert client.get("/live", params={"plan": 2}).json() is None
    assert client.post("/live/actions", params={"plan": 2}, json={"action": "purchase"}).status_code == 409
    client.post("/live/actions", params={"plan": 2}, json={"action": "land", "campaign": "xmas_fb"})
    client.post("/live/actions", params={"plan": 2}, json={"action": "product", "sku": "E01"})
    client.post("/live/actions", params={"plan": 2}, json={"action": "add", "sku": "E01"})
    j = client.post("/live/actions", params={"plan": 2}, json={"action": "purchase"}).json()
    assert [s["ev"] for s in j["steps"]] == ["page_view", "list_view", "page_view", "product_view", "add_to_cart",
                                             "page_view", "purchase"]
    assert len(j["orders"]) == 1
    assert j["orders"][0]["attribution"]["last"][0]["campaign"] == "xmas_fb"
    # v1 的 purchase 沒帶 order：同樣的操作，訂單接不回來
    client.post("/live/actions", params={"plan": 1}, json={"action": "land", "campaign": "xmas_fb"})
    client.post("/live/actions", params={"plan": 1}, json={"action": "add", "sku": "E01"})
    j1 = client.post("/live/actions", params={"plan": 1}, json={"action": "purchase"}).json()
    assert j1["orders"] == [] and j1["steps"][-1]["order_linked"] is False
    client.delete("/live")
    assert client.get("/live", params={"plan": 2}).json() is None
