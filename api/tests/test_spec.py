from typing import Any

import pytest
from pydantic import ValidationError

from app.spec import Project, RatioMetric, SimpleMetric


def test_example_project_loads(project: Project) -> None:
    assert "order_completed" in project.tracking_plan.events
    assert project.models["fct_orders"].grain == ["order_id"]
    assert isinstance(project.metric_layer.metrics["uv"], SimpleMetric)
    assert isinstance(project.metric_layer.metrics["conversion_rate"], RatioMetric)


def _assert_rejected(raw: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Project.model_validate(raw)


def test_rejects_unknown_property_type(raw_specs: dict[str, Any]) -> None:
    raw_specs["tracking_plan"]["events"]["add_to_cart"]["properties"]["quantity"]["type"] = "int"
    _assert_rejected(raw_specs, "quantity")


def test_rejects_property_named_like_envelope_field(raw_specs: dict[str, Any]) -> None:
    raw_specs["tracking_plan"]["events"]["login"]["properties"]["user_id"] = {"type": "string"}
    _assert_rejected(raw_specs, "保留欄位")


def test_rejects_non_snake_case_names(raw_specs: dict[str, Any]) -> None:
    raw_specs["metric_layer"]["metrics"]["Revenue"] = raw_specs["metric_layer"]["metrics"]["revenue"]
    _assert_rejected(raw_specs, "snake_case")


def test_rejects_entity_column_without_agg(raw_specs: dict[str, Any]) -> None:
    raw_specs["models"]["dim_sessions"]["columns"]["platform"] = {"from": "platform"}
    _assert_rejected(raw_specs, "需要 agg")


def test_rejects_fact_column_with_agg(raw_specs: dict[str, Any]) -> None:
    raw_specs["models"]["fct_orders"]["columns"]["revenue"]["agg"] = "sum"
    _assert_rejected(raw_specs, "不能有 agg")


def test_rejects_event_without_fires_on(raw_specs: dict[str, Any]) -> None:
    del raw_specs["tracking_plan"]["events"]["login"]["fires_on"]
    _assert_rejected(raw_specs, "fires_on")


def test_rejects_unknown_moment_and_context_field(raw_specs: dict[str, Any]) -> None:
    raw_specs["tracking_plan"]["events"]["login"]["fires_on"] = ["button_hover"]
    _assert_rejected(raw_specs, "button_hover")
    raw_specs["tracking_plan"]["events"]["login"]["fires_on"] = ["login_success"]
    raw_specs["tracking_plan"]["events"]["login"]["properties"]["method"]["from"] = "user.email"
    _assert_rejected(raw_specs, "user.email")


def test_rejects_metric_on_unknown_model(raw_specs: dict[str, Any]) -> None:
    raw_specs["metric_layer"]["metrics"]["revenue"]["model"] = "fct_payments"
    _assert_rejected(raw_specs, "fct_payments")


def test_rejects_ratio_of_ratio(raw_specs: dict[str, Any]) -> None:
    raw_specs["metric_layer"]["metrics"]["aov"]["numerator"] = "conversion_rate"
    _assert_rejected(raw_specs, "必須是 simple metric")


def test_rejects_report_with_unknown_metric(raw_specs: dict[str, Any]) -> None:
    raw_specs["metric_layer"]["reports"]["daily_overview"]["metrics"].append("gmv")
    _assert_rejected(raw_specs, "gmv")


def test_rejects_persona_continue_for_action_not_in_funnel(raw_specs: dict[str, Any]) -> None:
    raw_specs["simulation"]["funnel"] = raw_specs["simulation"]["funnel"][:2]
    _assert_rejected(raw_specs, "不在 funnel")


def test_loads_each_tracking_plan_version() -> None:
    from app.spec import load_project, read_tracking_plans

    from .conftest import SHOP_SPECS

    assert list(read_tracking_plans(SHOP_SPECS)) == [1, 2]
    assert load_project(SHOP_SPECS).tracking_plan.status == "current"
    assert "product_view" not in load_project(SHOP_SPECS, 2).tracking_plan.events
