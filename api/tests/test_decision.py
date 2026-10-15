from datetime import date

from app.checks import fanout, missing
from app.decision import decision_card


def card(cons, plans, questions, version, qid="budget"):
    q = questions[qid]
    return decision_card(q, plans[version], None, fanout(cons[version], q), missing(cons[version], plans[version], q),
                         date(2026, 12, 20))


def test_v1_card_asks_frontend_for_order(cons, plans, questions) -> None:
    text = card(cons, plans, questions, 1)
    assert "在 `purchase` 事件補上 `order`" in text and "最後點擊" in text and "2026-12-20" in text


def test_v2_card_has_no_frontend_todo(cons, plans, questions) -> None:
    text = card(cons, plans, questions, 2)
    assert "補上" not in text and "現行埋點已經足夠" in text


def test_category_card_has_no_attribution(cons, plans, questions) -> None:
    assert "歸因方式" not in card(cons, plans, questions, 1, "category")
