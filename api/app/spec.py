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

# 行為時刻：demo 商店與模擬器在這些時刻發出訊號，L1 用 fires_on 決定哪些事件在這個時刻送出。
# 行為和埋點分開，同一群使用者、同樣的行為，換一份 tracking plan 就會得到不同的事件。
Moment = Literal[
    "page_load",                 # 頁面載入完成（context: page.type）
    "product_detail_load",       # 商品詳情載入完成（product）
    "add_to_cart_click",         # 點擊「加入購物車」（product, item）
    "add_to_cart_success",       # 購物車 API 回傳成功（product, item, cart）
    "checkout_load",             # 結帳頁載入完成（cart）
    "payment_success",           # 付款成功、後端建立訂單（order, cart）
    "order_complete_page_load",  # 付款成功頁載入；使用者可能沒等到，也可能重新整理（order, cart）
    "login_success",             # 登入成功（login）
]
MOMENTS: tuple[str, ...] = Moment.__args__  # type: ignore[attr-defined]

# property 的值可以取自這些情境欄位；沒有寫 from 的 property，模擬器依型別隨機產生
ContextField = Literal[
    "session.platform",
    "page.type",
    "product.id",
    "product.price",
    "item.quantity",
    "cart.total",
    "cart.count",
    "order.id",
    "order.coupon_code",
    "login.method",
]


class Property(Spec):
    type: PropertyType
    description: str = ""
    enum: list[str] | None = None
    min: float | None = None
    max: float | None = None
    from_: ContextField | None = Field(None, alias="from")
    required: bool = True  # false：情境裡沒有值時可以不帶（例如沒用折扣碼）

    @model_validator(mode="after")
    def _enum_only_for_string(self) -> Property:
        if self.enum is not None and self.type != "string":
            raise ValueError("enum 只能用在 type: string")
        return self


class Event(Spec):
    description: str = ""
    trigger: str = ""
    fires_on: list[Moment] = Field(min_length=1)
    properties: dict[Ident, Property] = {}


class TrackingPlan(Spec):
    version: int
    name: str = ""
    status: Literal["current", "proposal", "retired"] = "proposal"
    description: str = ""
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

    def events_on(self, moment: str) -> list[str]:
        return [name for name, event in self.events.items() if moment in event.fires_on]


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

# 模擬器的使用者行為；每個行為會產生一或多個 moment
Action = Literal["view_home", "view_product", "add_to_cart", "checkout", "purchase"]


class FunnelStep(Spec):
    action: Action
    repeat: tuple[int, int] = (1, 1)


class Persona(Spec):
    weight: float = Field(gt=0)
    sessions_per_week: float = Field(gt=0)
    has_account_rate: float = Field(ge=0, le=1)
    platforms: list[str] = Field(min_length=1)
    continue_: dict[Action, float] = Field(default_factory=dict, alias="continue")


class Catalog(Spec):
    size: int = Field(gt=0)
    price_min: float = Field(gt=0)
    price_max: float = Field(gt=0)


class Login(Spec):
    rate_per_session: float = Field(ge=0, le=1)
    methods: list[str] = Field(default_factory=lambda: ["email", "google", "line"], min_length=1)


class Behavior(Spec):
    """和埋點無關、真實世界會發生的事；不同的埋點設計會「看到」不同的部分。"""

    add_to_cart_api_success: float = Field(1.0, ge=0, le=1)
    coupon_rate: float = Field(0.0, ge=0, le=1)
    coupon_codes: list[str] = []
    complete_page_reached: float = Field(1.0, ge=0, le=1)
    complete_page_reload_rate: float = Field(0.0, ge=0, le=1)


class Simulation(Spec):
    seed: int
    start_date: date
    days: int = Field(gt=0)
    visitors: int = Field(gt=0)
    catalog: Catalog
    login: Login | None = None
    behavior: Behavior = Behavior()
    funnel: list[FunnelStep] = Field(min_length=1)
    personas: dict[Ident, Persona] = Field(min_length=1)

    @model_validator(mode="after")
    def _coupons(self) -> Simulation:
        if self.behavior.coupon_rate > 0 and not self.behavior.coupon_codes:
            raise ValueError("coupon_rate > 0 時需要 coupon_codes")
        return self


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

        # model 的來源事件不在 L1 不算格式錯誤：換一份 tracking plan 時這很常見，由 R3 回報影響範圍
        for name in self.models:
            if name.startswith(("stg_", "rpt_")):
                errors.append(f"L3 model '{name}' 不能使用保留前綴 stg_ / rpt_")

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

        funnel_actions = {step.action for step in self.simulation.funnel}
        for name, persona in self.simulation.personas.items():
            errors += [
                f"simulation persona '{name}': continue 的 '{a}' 不在 funnel"
                for a in persona.continue_
                if a not in funnel_actions
            ]

        if errors:
            raise ValueError("\n".join(errors))
        return self


# L2–L4 與模擬設定每一層一個檔案；L1 可以有多個版本，放在 tracking_plans/
SPEC_FILES = {
    "staging": "l2_staging.yaml",
    "models": "l3_models.yaml",
    "metric_layer": "l4_metrics.yaml",
    "simulation": "simulation.yaml",
}
PLANS_DIR = "tracking_plans"


def _read_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def read_tracking_plans(spec_dir: Path) -> dict[int, dict[str, Any]]:
    """所有 tracking plan 版本的原始內容，依版本號排序。"""
    plans: dict[int, dict[str, Any]] = {}
    for path in sorted((spec_dir / PLANS_DIR).glob("*.yaml")):
        raw = _read_yaml(path)
        version = raw.get("version") if isinstance(raw, dict) else None
        if not isinstance(version, int):
            raise ValueError(f"{path.name} 沒有整數 version")
        if version in plans:
            raise ValueError(f"tracking plan version {version} 重複")
        plans[version] = raw
    if not plans:
        raise ValueError(f"{spec_dir / PLANS_DIR} 裡沒有 tracking plan")
    return dict(sorted(plans.items()))


def default_plan_version(plans: dict[int, dict[str, Any]]) -> int:
    """status: current 的版本；沒有的話用最新版。"""
    current = [v for v, raw in plans.items() if raw.get("status") == "current"]
    return current[-1] if current else max(plans)


def read_spec_files(spec_dir: Path, plan_version: int | None = None) -> dict[str, Any]:
    plans = read_tracking_plans(spec_dir)
    version = default_plan_version(plans) if plan_version is None else plan_version
    if version not in plans:
        raise KeyError(f"沒有 tracking plan v{version}（有：{', '.join(f'v{v}' for v in plans)}）")
    raw: dict[str, Any] = {"name": spec_dir.name, "tracking_plan": plans[version]}
    for key, filename in SPEC_FILES.items():
        raw[key] = _read_yaml(spec_dir / filename)
    return raw


def load_project(spec_dir: Path, plan_version: int | None = None) -> Project:
    return Project.model_validate(read_spec_files(spec_dir, plan_version))
