import pytest

from app.questions import dashboard


def test_three_questions(questions) -> None:
    assert list(questions) == ["budget", "assists", "category"]
    assert questions["category"].attribution is None


@pytest.mark.parametrize("model", ["last", "first", "linear"])
def test_budget_adds_up(cons, questions, model) -> None:
    board = dashboard(cons[2], questions["budget"], model)
    assert abs(sum(r["revenue"] for r in board["rows"]) - board["total"]) < 0.01
    assert board["rows"][-1]["campaign"].startswith("(")


def test_opener_and_closer_differ(cons, questions) -> None:
    rows = {r["campaign"]: r for r in dashboard(cons[2], questions["budget"], "last")["rows"]}
    assert rows["xmas_fb"]["first"] > rows["xmas_fb"]["last"]
    assert rows["xmas_gg"]["last"] > rows["xmas_gg"]["first"]


def test_assists(cons, questions) -> None:
    rows = dashboard(cons[2], questions["assists"])["rows"]
    assert all(r["assist_orders"] >= 0 for r in rows)
    assert any(r["assist_orders"] > 0 for r in rows)


def test_category_flags_high_view_low_conversion(cons, questions) -> None:
    board = dashboard(cons[1], questions["category"])
    assert any(r["flag"] for r in board["rows"])
    assert sum(c["units"] for c in board["categories"]) == sum(r["units"] for r in board["rows"])
