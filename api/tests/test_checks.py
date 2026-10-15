import pytest

from app.checks import fanout, missing


def test_missing_order_on_v1_only(cons, plans, questions) -> None:
    v1 = {(m["event"], m["param"]): m for m in missing(cons[1], plans[1], questions["budget"])}
    v2 = {(m["event"], m["param"]): m for m in missing(cons[2], plans[2], questions["budget"])}
    assert v1[("purchase", "order")]["status"] == "missing"
    assert v1[("purchase", "order")]["impact"]["value"] > 0
    assert v2[("purchase", "order")]["status"] == "ok"
    assert v2[("purchase", "order")]["impact"]["value"] == 0
    assert v1[("page_view", "utm_campaign")]["status"] == "partial"  # 只有從活動進站的第一頁有


@pytest.mark.parametrize("qid", ["budget", "assists", "category"])
def test_fanout_inflates(cons, questions, qid) -> None:
    result = fanout(cons[2], questions[qid])
    assert result["ratio"] > 1
    example = result["example"]
    assert example["rows_after"] >= 2
    if qid == "budget":
        assert example["rows_after"] == example["lines"] * example["touches"]
