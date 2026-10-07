"""依 L1 事件契約與 simulation 設定產生合成事件。只產生假資料。

每個 visitor 依 persona 抽樣 session 數，每個 session 沿著 funnel 前進，
每一步依 persona 的 continue 機率決定是否繼續。property 的值來源依序是：
funnel step 寫死的值 → bindings 指定的情境值（商品、購物車…）→ 依 L1 型別隨機產生。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .spec import Persona, Project, Property

# 一天中各小時的流量權重（晚上較高）
_HOUR_WEIGHTS = [1, 1, 1, 1, 1, 1, 2, 3, 4, 5, 5, 6, 7, 6, 5, 5, 6, 7, 8, 9, 10, 9, 6, 3]

Product = tuple[str, float]


@dataclass
class _SessionContext:
    platform: str
    product: Product | None = None
    viewed: list[Product] = field(default_factory=list)
    quantity: int | None = None
    cart: list[tuple[Product, int]] = field(default_factory=list)
    order_id: str | None = None

    def value(self, binding: str) -> Any:
        match binding:
            case "session.platform":
                return self.platform
            case "product.id":
                return self.product[0] if self.product else None
            case "product.price":
                return self.product[1] if self.product else None
            case "item.quantity":
                return self.quantity
            case "cart.total":
                return round(sum(price * qty for (_, price), qty in self.cart), 2)
            case "cart.count":
                return sum(qty for _, qty in self.cart)
            case "order.id":
                return self.order_id
        raise ValueError(f"未知的 binding: {binding}")


def _poisson(rng: random.Random, lam: float) -> int:
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p < limit:
            return k
        k += 1


def _fake(name: str, prop: Property, rng: random.Random) -> Any:
    if prop.enum:
        return rng.choice(prop.enum)
    match prop.type:
        case "integer":
            return rng.randint(int(prop.min or 0), int(prop.max or 10))
        case "number":
            return round(rng.uniform(prop.min or 0, prop.max or 1000), 2)
        case "boolean":
            return rng.random() < 0.5
    return f"{name}-{rng.getrandbits(24):06x}"


class _Generator:
    def __init__(self, project: Project) -> None:
        self.plan = project.tracking_plan
        self.sim = project.simulation
        self.rng = random.Random(self.sim.seed)
        catalog = self.sim.catalog
        self.catalog: list[Product] = [
            (f"p{i:03d}", float(round(self.rng.uniform(catalog.price_min, catalog.price_max))))
            for i in range(1, catalog.size + 1)
        ]
        self.start = datetime.combine(self.sim.start_date, datetime.min.time())

    def run(self) -> list[dict[str, Any]]:
        names = list(self.sim.personas)
        weights = [p.weight for p in self.sim.personas.values()]
        events: list[dict[str, Any]] = []
        for i in range(self.sim.visitors):
            persona = self.sim.personas[self.rng.choices(names, weights)[0]]
            events += self._visitor(i, persona)
        events.sort(key=lambda e: (e["timestamp"], e["event_id"]))
        return events

    def _visitor(self, index: int, persona: Persona) -> list[dict[str, Any]]:
        rng = self.rng
        user_id = f"u{index:05d}" if rng.random() < persona.has_account_rate else None
        anonymous_ids = {p: f"anon-{rng.getrandbits(40):010x}" for p in persona.platforms}
        logged_in: set[str] = set()  # 已登入過的裝置會保持登入
        n_sessions = _poisson(rng, persona.sessions_per_week * self.sim.days / 7)
        starts = sorted(self._session_start() for _ in range(n_sessions))
        events: list[dict[str, Any]] = []
        for start in starts:
            platform = rng.choice(persona.platforms)
            events += self._session(persona, start, platform, anonymous_ids[platform], user_id, logged_in)
        return events

    def _session_start(self) -> datetime:
        rng = self.rng
        return self.start + timedelta(
            days=rng.randrange(self.sim.days),
            hours=rng.choices(range(24), _HOUR_WEIGHTS)[0],
            minutes=rng.randrange(60),
            seconds=rng.randrange(60),
        )

    def _session(
        self,
        persona: Persona,
        start: datetime,
        platform: str,
        anonymous_id: str,
        user_id: str | None,
        logged_in: set[str],
    ) -> list[dict[str, Any]]:
        rng, sim = self.rng, self.sim
        ctx = _SessionContext(platform)
        current_user = user_id if platform in logged_in else None
        clock = start
        out: list[dict[str, Any]] = []

        def emit(event_name: str, fixed: dict[str, Any]) -> None:
            nonlocal clock
            out.append(
                {
                    "event_id": f"{rng.getrandbits(64):016x}",
                    "event_name": event_name,
                    "timestamp": clock.isoformat(sep=" "),
                    "anonymous_id": anonymous_id,
                    "user_id": current_user,
                    "tracking_plan_version": self.plan.version,
                    "properties": self._properties(event_name, fixed, ctx),
                }
            )
            clock += timedelta(seconds=rng.randint(5, 120))

        for index, step in enumerate(sim.funnel):
            if index > 0 and rng.random() >= persona.continue_.get(step.event, 1.0):
                break
            for _ in range(rng.randint(*step.repeat)):
                self._apply_action(step.action, ctx)
                emit(step.event, step.properties)
            if (
                index == 0
                and sim.login
                and user_id
                and current_user is None
                and rng.random() < sim.login.rate_per_session
            ):
                current_user = user_id
                logged_in.add(platform)
                emit(sim.login.event, {})
        return out

    def _apply_action(self, action: str | None, ctx: _SessionContext) -> None:
        rng = self.rng
        if action == "view_product":
            ctx.product = rng.choice(self.catalog)
            ctx.viewed.append(ctx.product)
            ctx.quantity = None
        elif action == "add_to_cart":
            ctx.product = rng.choice(ctx.viewed or self.catalog)
            ctx.quantity = rng.randint(1, 3)
            ctx.cart.append((ctx.product, ctx.quantity))
        elif action == "purchase":
            ctx.order_id = f"o{rng.getrandbits(40):010x}"

    def _properties(self, event_name: str, fixed: dict[str, Any], ctx: _SessionContext) -> dict[str, Any]:
        props: dict[str, Any] = {}
        for name, prop in self.plan.properties_of(event_name).items():
            value = fixed.get(name)
            if value is None and name in self.sim.bindings:
                value = ctx.value(self.sim.bindings[name])
            if value is None:
                value = _fake(name, prop, self.rng)
            props[name] = value
        return props


def product_catalog(project: Project) -> list[Product]:
    """和合成事件用的是同一份商品（同一個 seed），demo 商店也用它。"""
    return _Generator(project).catalog


def generate_events(project: Project) -> list[dict[str, Any]]:
    """依 simulation.seed 產生確定性的合成事件，依時間排序。"""
    return _Generator(project).run()
