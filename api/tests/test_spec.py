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


def test_rejects_model_with_unknown_source_event(raw_specs: dict[str, Any]) -> None:
    raw_specs["models"]["fct_orders"]["source"] = "purchase"
    _assert_rejected(raw_specs, "不是 L1 宣告的事件")


def test_rejects_metric_on_unknown_model(raw_specs: dict[str, Any]) -> None:
    raw_specs["metric_layer"]["metrics"]["revenue"]["model"] = "fct_payments"
    _assert_rejected(raw_specs, "fct_payments")


def test_rejects_ratio_of_ratio(raw_specs: dict[str, Any]) -> None:
    raw_specs["metric_layer"]["metrics"]["aov"]["numerator"] = "conversion_rate"
    _assert_rejected(raw_specs, "必須是 simple metric")


def test_rejects_report_with_unknown_metric(raw_specs: dict[str, Any]) -> None:
    raw_specs["metric_layer"]["reports"]["daily_overview"]["metrics"].append("gmv")
    _assert_rejected(raw_specs, "gmv")


def test_rejects_binding_to_undeclared_property(raw_specs: dict[str, Any]) -> None:
    raw_specs["simulation"]["bindings"]["discount"] = "cart.total"
    _assert_rejected(raw_specs, "discount")
