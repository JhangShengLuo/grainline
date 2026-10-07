"""L1–L4 規格與模擬設定的 schema，以及從 YAML 載入。

這裡只檢查「單一檔案內的格式」與「跨檔案的名稱參照」。欄位是否真的存在於上游 view
由 compiler 檢查（它知道每個 view 有哪些欄位）。
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    model_validator,
)

_IDENT = re.compile(r"[a-z][a-z0-9_]*")

# 每個原始事件都帶的信封欄位，由 SDK / ingest 負責，不在 L1 宣告
ENVELOPE_FIELDS = (
    "event_id",
    "event_name",
    "timestamp",
    "anonymous_id",
    "user_id",
    "tracking_plan_version",
)
# L2 推導出來的欄位
DERIVED_FIELDS = ("person_id", "session_id")


def _ident(value: str) -> str:
    if not _IDENT.fullmatch(value):
        raise ValueError(f"'{value}' 不是合法名稱（需為小寫 snake_case）")
    return value


Ident = Annotated[str, AfterValidator(_ident)]


class Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# ---------- L1 事件契約 ----------

PropertyType = Literal["string", "integer", "number", "boolean"]


class Property(Spec):
    type: PropertyType
    description: str = ""
    enum: list[str] | None = None
    min: float | None = None
    max: float | None = None

    @model_validator(mode="after")
    def _enum_only_for_string(self) -> Property:
        if self.enum is not None and self.type != "string":
            raise ValueError("enum 只能用在 type: string")
        return self


class Event(Spec):
    description: str = ""
    trigger: str = ""
    properties: dict[Ident, Property] = {}


class TrackingPlan(Spec):
    version: int
    common_properties: dict[Ident, Property] = {}
    events: dict[Ident, Event] = Field(min_length=1)

    @model_validator(mode="after")
    def _no_name_clash(self) -> TrackingPlan:
        reserved = set(ENVELOPE_FIELDS) | set(DERIVED_FIELDS)
        errors = [
            f"共用 property '{name}' 和保留欄位同名"
            for name in self.common_properties
            if name in reserved
        ]
        for event_name, event in self.events.items():
            for name in event.properties:
                if name in reserved or name in self.common_properties:
                    errors.append(f"事件 {event_name} 的 property '{name}' 和保留欄位或共用 property 同名")
        if errors:
            raise ValueError("\n".join(errors))
        return self

    def properties_of(self, event: str) -> dict[str, Property]:
        return {**self.common_properties, **self.events[event].properties}


# ---------- L2 解析 / Staging ----------


class Staging(Spec):
    session_timeout_minutes: int = Field(30, gt=0)
    identity: Literal["stitch_to_user", "anonymous_only"] = "stitch_to_user"


# ---------- L3 實體與事實模型 ----------


class Column(Spec):
    from_: Ident | None = Field(None, alias="from")
    agg: Literal["count", "count_distinct", "sum", "min", "max", "any_value"] | None = None

    @model_validator(mode="after")
    def _needs_source(self) -> Column:
        if self.from_ is None and self.agg != "count":
            raise ValueError("欄位需要 from（只有 agg: count 可以省略）")
        return self


class Model(Spec):
    kind: Literal["fact", "entity"]
    description: str = ""
    source: str  # L1 事件名稱，或 "*" 代表全部事件
    grain: list[Ident] = Field(min_length=1)
    columns: dict[Ident, Column] = Field(min_length=1)

    @model_validator(mode="after")
    def _grain_and_aggs(self) -> Model:
        errors = [f"grain 欄位 '{g}' 不在 columns 裡" for g in self.grain if g not in self.columns]
        for name, column in self.columns.items():
            if self.kind == "fact" and column.agg is not None:
                errors.append(f"fact 一列對應一個來源事件，欄位 '{name}' 不能有 agg")
            if self.kind == "entity":
                if name in self.grain and column.agg is not None:
                    errors.append(f"entity 的 grain 欄位 '{name}' 不能有 agg")
                if name not in self.grain and column.agg is None:
                    errors.append(f"entity 的非 grain 欄位 '{name}' 需要 agg")
        if errors:
            raise ValueError("\n".join(errors))
        return self


# ---------- L4 指標與報表 ----------

Scalar = str | int | float | bool


class Filter(Spec):
    column: Ident
    op: Literal["eq", "neq", "in", "not_in", "gt", "gte", "lt", "lte"]
    value: Scalar | list[Scalar]

    @model_validator(mode="after")
    def _value_shape(self) -> Filter:
        if (self.op in ("in", "not_in")) != isinstance(self.value, list):
            raise ValueError("op in / not_in 的 value 必須是 list，其他 op 必須是單一值")
        return self


class Measure(Spec):
    agg: Literal["count", "count_distinct", "sum", "avg", "min", "max"]
    column: Ident | None = None

    @model_validator(mode="after")
    def _needs_column(self) -> Measure:
        if self.column is None and self.agg != "count":
            raise ValueError(f"agg: {self.agg} 需要 column")
        return self


class SimpleMetric(Spec):
    type: Literal["simple"] = "simple"
    label: str
    description: str = ""
    model: Ident
    time_column: Ident
    measure: Measure
    additivity: Literal["additive", "semi_additive", "non_additive"]
    filters: list[Filter] = []


class RatioMetric(Spec):
    type: Literal["ratio"]
    label: str
    description: str = ""
    # rate：分子是分母的子集（轉換率），per_unit：每單位平均（客單價）
    kind: Literal["rate", "per_unit"] = "rate"
    numerator: Ident
    denominator: Ident
    additivity: Literal["non_additive"] = "non_additive"


TimeGrain = Literal["day", "week", "month"]


class RollupMetric(Spec):
    """先在 from_grain 算出 of，再用 agg 彙總到報表的 grain。例：週 UV = 日 UV 加總。"""

    type: Literal["rollup"]
    label: str
    description: str = ""
    of: Ident
    from_grain: TimeGrain
    agg: Literal["sum", "avg", "min", "max"]


def _metric_type(value: Any) -> str:
    if isinstance(value, dict):
        return value.get("type", "simple")
    return value.type


Metric = Annotated[
    Annotated[SimpleMetric, Tag("simple")]
    | Annotated[RatioMetric, Tag("ratio")]
    | Annotated[RollupMetric, Tag("rollup")],
    Discriminator(_metric_type),
]


class Report(Spec):
    label: str
    time_grain: TimeGrain
    dimensions: list[Ident] = []
    metrics: list[Ident] = Field(min_length=1)


class MetricLayer(Spec):
    metrics: dict[Ident, Metric] = Field(min_length=1)
    reports: dict[Ident, Report] = {}


# ---------- 模擬設定（只產生假資料）----------

Binding = Literal[
    "session.platform",
    "product.id",
    "product.price",
    "item.quantity",
    "cart.total",
    "cart.count",
    "order.id",
]


class FunnelStep(Spec):
    event: Ident
    repeat: tuple[int, int] = (1, 1)
    action: Literal["view_product", "add_to_cart", "purchase"] | None = None
    properties: dict[Ident, Scalar] = {}


class Persona(Spec):
    weight: float = Field(gt=0)
    sessions_per_week: float = Field(gt=0)
    has_account_rate: float = Field(ge=0, le=1)
    platforms: list[str] = Field(min_length=1)
    continue_: dict[Ident, float] = Field(default_factory=dict, alias="continue")


class Catalog(Spec):
    size: int = Field(gt=0)
    price_min: float = Field(gt=0)
    price_max: float = Field(gt=0)


class Login(Spec):
    event: Ident
    rate_per_session: float = Field(ge=0, le=1)


class Simulation(Spec):
    seed: int
    start_date: date
    days: int = Field(gt=0)
    visitors: int = Field(gt=0)
    catalog: Catalog
    login: Login | None = None
    funnel: list[FunnelStep] = Field(min_length=1)
    bindings: dict[Ident, Binding] = {}
    personas: dict[Ident, Persona] = Field(min_length=1)


# ---------- 整個專案 ----------


class Project(Spec):
    name: str
    tracking_plan: TrackingPlan
    staging: Staging
    models: dict[Ident, Model]
    metric_layer: MetricLayer
    simulation: Simulation

    @model_validator(mode="after")
    def _cross_references(self) -> Project:
        errors: list[str] = []
        events = self.tracking_plan.events

        for name, model in self.models.items():
            if name.startswith(("stg_", "rpt_")):
                errors.append(f"L3 model '{name}' 不能使用保留前綴 stg_ / rpt_")
            if model.source != "*" and model.source not in events:
                errors.append(f"L3 model '{name}': source '{model.source}' 不是 L1 宣告的事件")

        metrics = self.metric_layer.metrics
        for name, metric in metrics.items():
            if isinstance(metric, SimpleMetric):
                if metric.model not in self.models:
                    errors.append(f"L4 metric '{name}': model '{metric.model}' 不存在於 L3")
                continue
            if isinstance(metric, RollupMetric):
                if metric.of not in metrics:
                    errors.append(f"L4 metric '{name}': '{metric.of}' 不存在")
                elif isinstance(metrics[metric.of], RollupMetric):
                    errors.append(f"L4 metric '{name}': 不能 rollup 另一個 rollup '{metric.of}'")
                continue
            for part in (metric.numerator, metric.denominator):
                if part not in metrics:
                    errors.append(f"L4 metric '{name}': '{part}' 不存在")
                elif not isinstance(metrics[part], SimpleMetric):
                    errors.append(f"L4 metric '{name}': 比率的分子分母必須是 simple metric，'{part}' 不是")
        for name, report in self.metric_layer.reports.items():
            errors += [
                f"L4 report '{name}': metric '{m}' 不存在" for m in report.metrics if m not in metrics
            ]

        sim = self.simulation
        all_properties = set(self.tracking_plan.common_properties)
        for event in events.values():
            all_properties |= set(event.properties)
        for step in sim.funnel:
            if step.event not in events:
                errors.append(f"simulation funnel: 事件 '{step.event}' 不在 L1")
                continue
            declared = self.tracking_plan.properties_of(step.event)
            errors += [
                f"simulation funnel: 事件 '{step.event}' 沒有 property '{p}'"
                for p in step.properties
                if p not in declared
            ]
        if sim.login and sim.login.event not in events:
            errors.append(f"simulation login: 事件 '{sim.login.event}' 不在 L1")
        errors += [
            f"simulation bindings: property '{p}' 不在 L1" for p in sim.bindings if p not in all_properties
        ]
        funnel_events = {step.event for step in sim.funnel}
        for name, persona in sim.personas.items():
            errors += [
                f"simulation persona '{name}': continue 的 '{e}' 不在 funnel"
                for e in persona.continue_
                if e not in funnel_events
            ]

        if errors:
            raise ValueError("\n".join(errors))
        return self


# 每一層一個檔案，方便各角色只改自己負責的那份
SPEC_FILES = {
    "tracking_plan": "l1_tracking_plan.yaml",
    "staging": "l2_staging.yaml",
    "models": "l3_models.yaml",
    "metric_layer": "l4_metrics.yaml",
    "simulation": "simulation.yaml",
}


def read_spec_files(spec_dir: Path) -> dict[str, Any]:
    raw: dict[str, Any] = {"name": spec_dir.name}
    for key, filename in SPEC_FILES.items():
        raw[key] = yaml.safe_load((spec_dir / filename).read_text(encoding="utf-8"))
    return raw


def load_project(spec_dir: Path) -> Project:
    return Project.model_validate(read_spec_files(spec_dir))
