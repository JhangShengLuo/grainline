"""M11：傳統做法 vs Grainline。"""

from fastapi.testclient import TestClient

from app.story import across_markets

from .conftest import load_small
from .test_api import client, data_dir, spec_dir  # noqa: F401  共用 fixture


def test_story_numbers_come_from_the_simulator(client: TestClient) -> None:
    body = client.get("/story").json()
    assert "示意" in body["note"]
    steps = {s["week"]: s for s in body["traditional"]["steps"]}
    assert "兩種定義差" in steps[2]["impact"]
    assert "加購次數灌水" in steps[3]["impact"]
    assert "product_view" in steps[5]["impact"]
    assert body["traditional"]["hits"] <= body["grainline"]["hits"]
    assert len(body["traditional"]["picks"]) == 8  # 首頁放銷量前 8 名
    assert body["grainline"]["compute_seconds"] > 0
    # 只有建議與標記，沒有真相數值
    assert "value_per_view" not in str(body)


def test_story_is_cached(client: TestClient) -> None:
    assert client.get("/story").json() == client.get("/story").json()


def test_across_markets_compares_playbook_and_traditional() -> None:
    result = across_markets(load_small(), seeds=(1, 2), replicates=2)
    assert [m["seed"] for m in result["markets"]] == [1, 2]
    for m in result["markets"]:
        assert 0 <= m["playbook_hits"] <= result["k"] and 0 <= m["traditional_hits"] <= result["k"]
    assert 0 <= result["wins"] <= 2
