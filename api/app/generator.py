"""依模擬設定產生使用者行為，再依 L1 的 fires_on 把行為轉成事件。只產生假資料。

行為和埋點是分開的：模擬器只決定「使用者做了什麼」（行為時刻 moment 與當下情境），
L1 決定「哪些時刻要送哪些事件、property 的值從哪裡來」。所以同一個 seed 產生的是同一群人、
同樣的行為；換一份 tracking plan，只有記錄下來的事件不同。
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
class Context:
    """一個 session 當下的情境，L1 property 的 from 就是取這裡的值。"""

    platform: str
    page_type: str | None = None
    product: Product | None = None
    viewed: list[Product] = field(default_factory=list)
    quantity: int | None = None
    cart: list[tuple[Product, int]] = field(default_factory=list)
    order_id: str | None = None
    coupon_code: str | None = None
    login_method: str | None = None

    def value(self, path: str) -> Any:
        match path:
            case "session.platform":
                return self.platform
            case "page.type":
                return self.page_type
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
            case "order.coupon_code":
                return self.coupon_code
            case "login.method":
                return self.login_method
        raise ValueError(f"未知的情境欄位: {path}")


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
        self.behavior = self.sim.behavior
        self.rng = random.Random(self.sim.seed)
        catalog = self.sim.catalog
        self.catalog: list[Product] = [
            (f"p{i:03d}", float(round(self.rng.uniform(catalog.price_min, catalog.price_max))))
            for i in range(1, catalog.size + 1)
        ]
        self.start = datetime.combine(self.sim.start_date, datetime.min.time())
        # 埋點用的亂數和行為用的亂數分開：換 tracking plan 不會改變使用者的行為
        self.fake_rng = random.Random(self.sim.seed + 1)

    def run(self) -> list[dict[str, Any]]:
        names = list(self.sim.personas)
        weights = [p.weight for p in self.sim.personas.values()]
        events: list[dict[str, Any]] = []
        for i in range(self.sim.visitors):
            persona = self.sim.personas[self.rng.choices(names, weights)[0]]
            events += self._visitor(i, persona)
        # 觀察期在最後一天午夜結束（像資料快照）：跨過午夜的 session 只留下之前的部分
        end = (self.start + timedelta(days=self.sim.days)).isoformat(sep=" ")
        events = [e for e in events if e["timestamp"] < end]
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
        last_end: datetime | None = None
        for start in starts:
            # 同一個人一次只會有一個 session；前一個還沒結束就往後延
            if last_end is not None and start <= last_end:
                start = last_end + timedelta(minutes=rng.randint(1, 30))
            platform = rng.choice(persona.platforms)
            session, last_end = self._session(persona, start, platform, anonymous_ids[platform], user_id, logged_in)
            events += session
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
    ) -> tuple[list[dict[str, Any]], datetime]:
        """回傳 (事件, session 結束時間)。"""
        rng, sim = self.rng, self.sim
        ctx = Context(platform)
        current_user = user_id if platform in logged_in else None
        clock = start
        out: list[dict[str, Any]] = []

        def moment(name: str) -> None:
            """行為時刻：送出 L1 裡 fires_on 這個時刻的所有事件，然後時間往前走。"""
            nonlocal clock
            for event_name in self.plan.events_on(name):
                out.append({
                    "event_id": f"{self.fake_rng.getrandbits(64):016x}",
                    "event_name": event_name,
                    "timestamp": clock.isoformat(sep=" "),
                    "anonymous_id": anonymous_id,
                    "user_id": current_user,
                    "tracking_plan_version": self.plan.version,
                    "properties": self._properties(event_name, ctx),
                })
            clock += timedelta(seconds=rng.randint(5, 120))

        for index, step in enumerate(sim.funnel):
            if index > 0 and rng.random() >= persona.continue_.get(step.action, 1.0):
                break
            if step.action in ("checkout", "purchase") and not ctx.cart:
                break  # 加入購物車全部失敗：沒有東西可以結帳
            for _ in range(rng.randint(*step.repeat)):
                self._act(step.action, ctx, moment)
            if index == 0 and sim.login and user_id and current_user is None and rng.random() < sim.login.rate_per_session:
                current_user = user_id
                logged_in.add(platform)
                ctx.login_method = rng.choice(sim.login.methods)
                moment("login_success")
        return out, clock

    def _act(self, action: str, ctx: Context, moment: Any) -> None:
        rng, behavior = self.rng, self.behavior
        if action == "view_home":
            ctx.page_type, ctx.product = "home", None
            moment("page_load")
        elif action == "view_product":
            ctx.page_type, ctx.product, ctx.quantity = "product", rng.choice(self.catalog), None
            ctx.viewed.append(ctx.product)
            moment("page_load")
            moment("product_detail_load")
        elif action == "add_to_cart":
            ctx.product = rng.choice(ctx.viewed or self.catalog)
            ctx.quantity = rng.randint(1, 3)
            moment("add_to_cart_click")
            if rng.random() < behavior.add_to_cart_api_success:
                ctx.cart.append((ctx.product, ctx.quantity))
                moment("add_to_cart_success")
        elif action == "checkout":
            ctx.page_type, ctx.product = "checkout", None
            moment("page_load")
            moment("checkout_load")
        elif action == "purchase":
            ctx.order_id = f"o{rng.getrandbits(40):010x}"
            ctx.coupon_code = rng.choice(behavior.coupon_codes) if rng.random() < behavior.coupon_rate else None
            moment("payment_success")
            if rng.random() < behavior.complete_page_reached:
                moment("order_complete_page_load")
                if rng.random() < behavior.complete_page_reload_rate:
                    moment("order_complete_page_load")

    def _properties(self, event_name: str, ctx: Context) -> dict[str, Any]:
        """有 from 的 property 取情境值（情境裡沒有就不帶）；沒有 from 的依型別隨機產生。"""
        props: dict[str, Any] = {}
        for name, prop in self.plan.properties_of(event_name).items():
            value = ctx.value(prop.from_) if prop.from_ else _fake(name, prop, self.fake_rng)
            if value is not None:
                props[name] = value
        return props


def product_catalog(project: Project) -> list[Product]:
    """和合成事件用的是同一份商品（同一個 seed），demo 商店也用它。"""
    return _Generator(project).catalog


def generate_events(project: Project) -> list[dict[str, Any]]:
    """依 simulation.seed 產生確定性的合成事件，依時間排序。"""
    return _Generator(project).run()
