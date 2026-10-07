import duckdb
import pytest

from app.compiler import CompileError, compile_project
from app.generator import generate_events
from app.spec import Project
from app.warehouse import build_warehouse

from .conftest import event


def _scalar(con: duckdb.DuckDBPyConnection, sql: str) -> object:
    return con.execute(sql).fetchone()[0]


# ---------- L2：用手工事件驗證精確行為 ----------


def test_session_splits_after_timeout(con: duckdb.DuckDBPyConnection, project: Project) -> None:
    build_warehouse(
        con,
        project,
        [
            event("page_view", "2026-09-01 10:00:00", page_type="home"),
            event("page_view", "2026-09-01 10:29:00", page_type="home"),  # 間隔 29 分：同一個 session
            event("page_view", "2026-09-01 11:00:00", page_type="home"),  # 間隔 31 分：新 session
        ],
    )
    sessions = [r[0] for r in con.execute("select session_id from stg_events order by timestamp").fetchall()]
    assert sessions[0] == sessions[1] != sessions[2]


def test_identity_stitching_is_retroactive(con: duckdb.DuckDBPyConnection, project: Project) -> None:
    build_warehouse(
        con,
        project,
        [
            event("page_view", "2026-09-01 10:00:00", page_type="home"),
            event("login", "2026-09-01 10:01:00", user_id="u1", method="email"),
            event("page_view", "2026-09-01 10:02:00", user_id="u1", page_type="product"),
            event("page_view", "2026-09-02 09:00:00", anonymous_id="anon-2", user_id="u1", page_type="home"),
        ],
    )
    assert _scalar(con, "select count(distinct person_id) from stg_events") == 1
    assert _scalar(con, "select person_id from stg_events order by timestamp limit 1") == "u1"


def test_anonymous_only_identity_keeps_devices_apart(
    con: duckdb.DuckDBPyConnection, project: Project
) -> None:
    project = project.model_copy(
        update={"staging": project.staging.model_copy(update={"identity": "anonymous_only"})}
    )
    build_warehouse(
        con,
        project,
        [
            event("login", "2026-09-01 10:01:00", user_id="u1", method="email"),
            event("page_view", "2026-09-02 09:00:00", anonymous_id="anon-2", user_id="u1", page_type="home"),
        ],
    )
    assert _scalar(con, "select count(distinct person_id) from stg_events") == 2


def test_unknown_events_are_quarantined(con: duckdb.DuckDBPyConnection, project: Project) -> None:
    build_warehouse(
        con,
        project,
        [
            event("page_view", "2026-09-01 10:00:00", page_type="home"),
            event("wishlist_add", "2026-09-01 10:01:00", product_id="p001"),
        ],
    )
    assert _scalar(con, "select count(*) from stg_events") == 1
    assert _scalar(con, "select event_name from stg_unknown_events") == "wishlist_add"


def test_properties_are_typed_and_bad_values_become_null(
    con: duckdb.DuckDBPyConnection, project: Project
) -> None:
    build_warehouse(
        con,
        project,
        [
            event("add_to_cart", "2026-09-01 10:00:00", product_id="p001", price=399, quantity=2),
            event("add_to_cart", "2026-09-01 10:01:00", product_id="p002", price="free", quantity=1),
        ],
    )
    rows = con.execute("select price, quantity from stg_add_to_cart order by timestamp").fetchall()
    assert rows == [(399.0, 2), (None, 1)]
    types = dict(con.execute("select column_name, column_type from (describe stg_add_to_cart)").fetchall())
    assert types["price"] == "DOUBLE" and types["quantity"] == "BIGINT"


# ---------- L4：報表數字 ----------


def test_ratio_is_computed_from_period_totals(con: duckdb.DuckDBPyConnection, project: Project) -> None:
    build_warehouse(
        con,
        project,
        [
            event("page_view", "2026-09-01 10:00:00", "a", page_type="home"),
            event("page_view", "2026-09-01 11:00:00", "b", page_type="home"),
            event("page_view", "2026-09-01 12:00:00", "c", page_type="home"),
            event("page_view", "2026-09-01 13:00:00", "d", page_type="home"),
            event("order_completed", "2026-09-01 10:05:00", "a", order_id="o1", revenue=1000, item_count=1),
            event("order_completed", "2026-09-01 10:09:00", "a", order_id="o2", revenue=500, item_count=1),
        ],
    )
    row = con.execute(
        "select uv, orders, revenue, conversion_rate, aov from rpt_daily_overview"
    ).fetchone()
    assert row == (4, 2, 1500.0, 0.25, 750.0)


def test_days_without_orders_show_zero_not_null(
    con: duckdb.DuckDBPyConnection, project: Project
) -> None:
    build_warehouse(con, project, [event("page_view", "2026-09-01 10:00:00", page_type="home")])
    row = con.execute("select uv, orders, revenue, conversion_rate, aov from rpt_daily_overview").fetchone()
    assert row == (1, 0, 0.0, 0.0, None)


@pytest.fixture
def shop(con: duckdb.DuckDBPyConnection, project: Project) -> duckdb.DuckDBPyConnection:
    build_warehouse(con, project, generate_events(project))
    return con


def test_fact_grain_is_unique(shop: duckdb.DuckDBPyConnection, project: Project) -> None:
    for name, model in project.models.items():
        grain = ", ".join(model.grain)
        duplicates = _scalar(shop, f"select count(*) from (select {grain} from {name} group by all having count(*) > 1)")
        assert duplicates == 0, name


def test_report_totals_match_facts(shop: duckdb.DuckDBPyConnection) -> None:
    assert _scalar(shop, "select sum(revenue) from rpt_daily_overview") == pytest.approx(
        _scalar(shop, "select sum(revenue) from fct_orders")
    )
    assert _scalar(shop, "select sum(orders) from rpt_daily_overview") == _scalar(
        shop, "select count(*) from fct_orders"
    )


def test_weekly_uv_is_not_the_sum_of_daily_uv(shop: duckdb.DuckDBPyConnection) -> None:
    """M2 的 R1 要抓的衝突：在假資料上確實存在。"""
    weekly = _scalar(
        shop,
        "select sum(uv) from (select date_trunc('week', ts) w, count(distinct person_id) uv from fct_page_views group by w)",
    )
    daily = _scalar(shop, "select sum(uv) from rpt_daily_overview")
    assert weekly < daily


# ---------- 編譯錯誤 ----------


def test_model_referencing_undeclared_property_fails_to_compile(project: Project) -> None:
    model = project.models["fct_orders"]
    columns = {**model.columns, "coupon": model.columns["revenue"].model_copy(update={"from_": "coupon_code"})}
    broken = project.model_copy(
        update={"models": {**project.models, "fct_orders": model.model_copy(update={"columns": columns})}}
    )
    with pytest.raises(CompileError, match="coupon_code"):
        compile_project(broken)


def test_report_dimension_missing_on_model_fails_to_compile(project: Project) -> None:
    reports = project.metric_layer.reports
    report = reports["weekly_by_platform"].model_copy(update={"dimensions": ["page_type"]})
    broken = project.model_copy(
        update={
            "metric_layer": project.metric_layer.model_copy(
                update={"reports": {**reports, "weekly_by_platform": report}}
            )
        }
    )
    with pytest.raises(CompileError, match="page_type") as info:
        compile_project(broken)
    # page_type 只在 fct_page_views，其他 model 都應該被列出來
    assert len(info.value.errors) >= 3
