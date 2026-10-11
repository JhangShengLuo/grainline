"""模擬一個有真相的市場：使用者行為 → 行為時刻 → 依 L1 的 fires_on 轉成事件；後端同時產生業務資料。只產生假資料。

行為和埋點是分開的：模擬器只決定「使用者做了什麼」（行為時刻 moment 與當下情境），
L1 決定「哪些時刻要送哪些事件、property 的值從哪裡來」。同一個 seed 產生的是同一群人、
同樣的行為；換一份 tracking plan，只有記錄下來的事件不同，後端的業務資料完全一樣。

市場的真相（每個商品真實的吸引力、轉換傾向、趨勢）只存在模擬器裡，報表看不到。
首頁精選與分類排序依「舊排序」決定、不看真實品質，所以有好商品被埋沒——這就是要找的潛力商品。
"""

from __future__ import annotations

import heapq
import math
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .spec import Persona, Project, Property

# 一天中各小時的流量權重（晚上較高）
_HOUR_WEIGHTS = [1, 1, 1, 1, 1, 1, 2, 3, 4, 5, 5, 6, 7, 6, 5, 5, 6, 7, 8, 9, 10, 9, 6, 3]

_ADJECTIVES = ["經典", "輕量", "復古", "機能", "極簡", "手工", "日系", "北歐"]
_NOUNS = [
    ("帆布鞋", "鞋款"),
    ("慢跑鞋", "鞋款"),
    ("托特包", "包袋"),
    ("後背包", "包袋"),
    ("棉 T", "服飾"),
    ("牛仔外套", "服飾"),
    ("保溫瓶", "生活"),
    ("馬克杯", "生活"),
    ("藍牙耳機", "3C"),
    ("行動電源", "3C"),
]


@dataclass(eq=False)  # 用身分比較與雜湊：每個商品只有一個物件
class ProductTruth:
    """一個商品：公開的屬性，加上只有模擬器知道的真相。"""

    id: str
    name: str
    category: str
    price: float
    cost: float
    launch_day: int            # 第幾天上架（0 = 期間開始前就在賣）
    legacy_rank: int           # 舊排序：決定首頁精選與分類頁順序，不看真實品質
    attractiveness: float      # 真相：商品卡片被點的吸引力
    conversion: float          # 真相：看了商品頁之後加購的傾向
    rising: bool               # 真相：吸引力是否隨時間上升
    initial_stock: int

    def attract_at(self, day: float, days: int, growth: float) -> float:
        return self.attractiveness * (1 + growth * day / days) if self.rising else self.attractiveness


@dataclass
class Context:
    """一個 session 當下的情境，L1 property 的 from 就是取這裡的值。"""

    platform: str
    page_type: str | None = None
    list_name: str | None = None
    list_position: int | None = None
    product: ProductTruth | None = None
    viewed: list[ProductTruth] = field(default_factory=list)
    seen: list[tuple[ProductTruth, int]] = field(default_factory=list)  # (商品, 位置)
    quantity: int | None = None
    cart: list[tuple[ProductTruth, int]] = field(default_factory=list)
    order_id: str | None = None
    coupon_code: str | None = None
    login_method: str | None = None

    def value(self, path: str) -> Any:
        match path:
            case "session.platform":
                return self.platform
            case "page.type":
                return self.page_type
            case "list.name":
                return self.list_name
            case "list.position":
                return self.list_position
            case "product.id":
                return self.product.id if self.product else None
            case "product.price":
                return self.product.price if self.product else None
            case "item.quantity":
                return self.quantity
            case "cart.total":
                return round(sum(p.price * qty for p, qty in self.cart), 2)
            case "cart.count":
                return sum(qty for _, qty in self.cart)
            case "order.id":
                return self.order_id
            case "order.coupon_code":
                return self.coupon_code
            case "login.method":
                return self.login_method
        raise ValueError(f"未知的情境欄位: {path}")


@dataclass(frozen=True)
class Scenario:
    """模擬執行用的情境：同一個商品世界（同一份真相），換一組使用者行為的隨機情境、往後模擬幾天、可以改首頁精選。"""

    behavior_seed: int                     # 使用者行為的隨機情境
    offset_days: int                       # 從原本期間開始算，往後第幾天開始
    days: int                              # 模擬幾天
    featured: tuple[str, ...] | None = None  # 首頁精選覆寫（依位置）；None = 維持舊排序
    record_events: bool = False            # 只要後端資料時不產生前端事件，快很多


@dataclass
class World:
    """模擬的結果：前端事件、後端的業務資料，以及只有模擬器知道的真相。"""

    events: list[dict[str, Any]]
    tables: dict[str, list[dict[str, Any]]]
    truth: list[dict[str, Any]]


@dataclass
class _Visitor:
    persona: Persona
    user_id: str | None
    anonymous_ids: dict[str, str]
    logged_in: set[str] = field(default_factory=set)
    last_end: datetime | None = None


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


def _lognormal(rng: random.Random, sigma: float) -> float:
    """平均為 1 的對數常態。"""
    return math.exp(rng.gauss(-sigma * sigma / 2, sigma))


Moment = Callable[[str], None]


class _Generator:
    def __init__(self, project: Project, scenario: Scenario | None = None) -> None:
        self.plan = project.tracking_plan
        self.sim = project.simulation
        self.market = self.sim.market
        self.behavior = self.sim.behavior
        self.rng = random.Random(self.sim.seed)
        # 商品世界永遠由 simulation.seed 決定；情境只換使用者行為
        self.products = self._make_products()
        self.scenario = scenario
        self.offset = scenario.offset_days if scenario else 0
        self.days = scenario.days if scenario else self.sim.days
        self.record_events = scenario.record_events if scenario else True
        if scenario:
            self.rng = random.Random(scenario.behavior_seed)
        self.start = datetime.combine(self.sim.start_date, datetime.min.time()) + timedelta(days=self.offset)
        self.end = self.start + timedelta(days=self.days)
        self.categories = sorted({p.category for p in self.products})
        # 埋點用的亂數和行為用的亂數分開：換 tracking plan 不會改變使用者的行為
        self.fake_rng = random.Random(self.sim.seed + 1)
        self.stock = {p.id: p.initial_stock for p in self.products}
        self.order_lines: list[dict[str, Any]] = []
        self.inventory: list[dict[str, Any]] = []
        self.impressions = {p.id: 0 for p in self.products}
        self._snapshot_day = 0

    # ---------- 商品 ----------

    def _make_products(self) -> list[ProductTruth]:
        rng, catalog, market = self.rng, self.sim.catalog, self.market
        n = catalog.size
        legacy = list(range(1, n + 1))
        rng.shuffle(legacy)
        new_products = set(rng.sample(range(n), k=max(1, n // 12)))  # 期間中才上架的新品
        rising = set(rng.sample(range(n), k=round(n * market.rising_share)))
        products = []
        for i in range(n):
            noun, category = _NOUNS[i % len(_NOUNS)]
            price = float(round(rng.uniform(catalog.price_min, catalog.price_max)))
            launch_day = rng.randint(3, max(3, self.sim.days - 7)) if i in new_products else 0
            products.append(ProductTruth(
                id=f"p{i + 1:03d}",
                name=f"{_ADJECTIVES[(i // len(_NOUNS)) % len(_ADJECTIVES)]}{noun}",
                category=category,
                price=price,
                cost=float(round(price * rng.uniform(*catalog.cost_ratio))),
                launch_day=launch_day,
                # 新品還沒被推，排在舊排序最後面
                legacy_rank=legacy[i] + (n if launch_day else 0),
                attractiveness=_lognormal(rng, market.attractiveness_sigma),
                conversion=_lognormal(rng, market.conversion_sigma),
                rising=i in rising,
                initial_stock=rng.randint(*market.initial_stock),
            ))
        return products

    def available(self, day: int) -> list[ProductTruth]:
        return sorted((p for p in self.products if p.launch_day <= day + self.offset), key=lambda p: p.legacy_rank)

    def featured(self, day: int) -> list[ProductTruth]:
        if self.scenario and self.scenario.featured is not None:
            launched = {p.id: p for p in self.available(day)}
            return [launched[i] for i in self.scenario.featured if i in launched]
        return self.available(day)[: self.market.featured_slots]

    def category_list(self, category: str, day: int) -> list[ProductTruth]:
        return [p for p in self.available(day) if p.category == category]

    # ---------- 庫存（依時間推進）----------

    def _advance_inventory(self, day: int) -> None:
        """記下每天結束時的庫存快照並定期補貨，直到 day 的前一天。"""
        while self._snapshot_day < day:
            snapshot_date = (self.start + timedelta(days=self._snapshot_day)).date().isoformat()
            for p in self.products:
                if p.launch_day <= self._snapshot_day + self.offset:
                    self.inventory.append({"date": snapshot_date, "product_id": p.id, "on_hand": self.stock[p.id]})
            self._snapshot_day += 1
            if self._snapshot_day % self.market.restock_days == 0:
                for p in self.products:
                    self.stock[p.id] = max(self.stock[p.id], p.initial_stock)

    # ---------- 主流程 ----------

    def run(self) -> World:
        rng = self.rng
        names = list(self.sim.personas)
        weights = [p.weight for p in self.sim.personas.values()]
        queue: list[tuple[datetime, int, int]] = []
        visitors: list[_Visitor] = []
        seq = 0
        for i in range(self.sim.visitors):
            persona = self.sim.personas[rng.choices(names, weights)[0]]
            visitors.append(_Visitor(
                persona=persona,
                user_id=f"u{i:05d}" if rng.random() < persona.has_account_rate else None,
                anonymous_ids={p: f"anon-{rng.getrandbits(40):010x}" for p in persona.platforms},
            ))
            for _ in range(_poisson(rng, persona.sessions_per_week * self.days / 7)):
                seq += 1
                heapq.heappush(queue, (self._session_start(), seq, i))

        # 依時間先後處理所有 session：庫存要照真實順序扣
        events: list[dict[str, Any]] = []
        while queue:
            start, _, i = heapq.heappop(queue)
            visitor = visitors[i]
            if start >= self.end:
                continue
            # 同一個人一次只會有一個 session；前一個還沒結束就往後延
            if visitor.last_end is not None and start <= visitor.last_end:
                seq += 1
                heapq.heappush(queue, (visitor.last_end + timedelta(minutes=rng.randint(1, 30)), seq, i))
                continue
            self._advance_inventory((start - self.start).days)
            session, visitor.last_end = self._session(visitor, start)
            events += session
        self._advance_inventory(self.days)

        # 觀察期在最後一天午夜結束（像資料快照）：跨過午夜的 session 只留下之前的部分
        end = self.end.isoformat(sep=" ")
        events = [e for e in events if e["timestamp"] < end]
        events.sort(key=lambda e: (e["timestamp"], e["event_id"]))
        lines = [r for r in self.order_lines if r["ordered_at"] < end]
        lines.sort(key=lambda r: (r["ordered_at"], r["order_id"], r["line_no"]))
        return World(
            events=events,
            tables={"products": self.product_rows(), "order_lines": lines, "inventory_daily": self.inventory},
            truth=self._truth(),
        )

    def _session_start(self) -> datetime:
        rng = self.rng
        return self.start + timedelta(
            days=rng.randrange(self.days),
            hours=rng.choices(range(24), _HOUR_WEIGHTS)[0],
            minutes=rng.randrange(60),
            seconds=rng.randrange(60),
        )

    def _session(self, visitor: _Visitor, start: datetime) -> tuple[list[dict[str, Any]], datetime]:
        """回傳 (事件, session 結束時間)。"""
        rng, sim = self.rng, self.sim
        persona = visitor.persona
        platform = rng.choice(persona.platforms)
        anonymous_id = visitor.anonymous_ids[platform]
        ctx = Context(platform)
        user: list[str | None] = [visitor.user_id if platform in visitor.logged_in else None]
        clock = [start]
        out: list[dict[str, Any]] = []

        def moment(name: str) -> None:
            """行為時刻：送出 L1 裡 fires_on 這個時刻的所有事件，然後時間往前走。"""
            for event_name in self.plan.events_on(name) if self.record_events else ():
                out.append({
                    "event_id": f"{self.fake_rng.getrandbits(64):016x}",
                    "event_name": event_name,
                    "timestamp": clock[0].isoformat(sep=" "),
                    "anonymous_id": anonymous_id,
                    "user_id": user[0],
                    "tracking_plan_version": self.plan.version,
                    "properties": self._properties(event_name, ctx),
                })
            if name != "product_impression":  # 同一個畫面上的曝光是同時發生的
                clock[0] += timedelta(seconds=rng.randint(5, 120))

        def backend_order() -> None:
            """後端建立訂單：寫入訂單明細、扣庫存。和前端埋點無關。"""
            for line_no, (product, qty) in enumerate(ctx.cart, start=1):
                self.order_lines.append({
                    "order_id": ctx.order_id,
                    "line_no": line_no,
                    "product_id": product.id,
                    "quantity": qty,
                    "unit_price": product.price,
                    "line_revenue": product.price * qty,
                    "coupon_code": ctx.coupon_code,
                    "ordered_at": clock[0].isoformat(sep=" "),
                    "anonymous_id": anonymous_id,
                    "user_id": user[0],
                })
                self.stock[product.id] = max(0, self.stock[product.id] - qty)

        for index, step in enumerate(sim.funnel):
            if index > 0 and rng.random() >= persona.continue_.get(step.action, 1.0):
                if step.optional:
                    continue
                break
            if step.action in ("checkout", "purchase") and not ctx.cart:
                break  # 沒有東西可以結帳
            for _ in range(rng.randint(*step.repeat)):
                self._act(step.action, ctx, moment, backend_order, clock[0])
            if index == 0 and sim.login and visitor.user_id and user[0] is None \
                    and rng.random() < sim.login.rate_per_session:
                user[0] = visitor.user_id
                visitor.logged_in.add(platform)
                ctx.login_method = rng.choice(sim.login.methods)
                moment("login_success")
        return out, clock[0]

    def _show_list(self, ctx: Context, list_name: str, products: list[ProductTruth], moment: Moment) -> None:
        for position, product in enumerate(products, start=1):
            ctx.product, ctx.list_name, ctx.list_position = product, list_name, position
            ctx.seen.append((product, position))
            self.impressions[product.id] += 1
            moment("product_impression")
        ctx.product = ctx.list_name = ctx.list_position = None

    def _act(self, action: str, ctx: Context, moment: Moment, backend_order: Callable[[], None], clock: datetime) -> None:
        rng, behavior, market = self.rng, self.behavior, self.market
        day = (clock - self.start).total_seconds() / 86400
        today = int(day)
        if action == "view_home":
            ctx.page_type, ctx.product = "home", None
            moment("page_load")
            self._show_list(ctx, "home_featured", self.featured(today), moment)
        elif action == "browse_category":
            category = rng.choice(self.categories)
            ctx.page_type, ctx.product = "category", None
            moment("page_load")
            depth = rng.randint(*market.list_depth)
            self._show_list(ctx, "category", self.category_list(category, today)[:depth], moment)
        elif action == "view_product":
            # 點哪個商品：看過的卡片裡依真實吸引力與位置；沒看過任何卡片就從全部商品挑
            candidates = ctx.seen or [(p, 1) for p in self.available(today)]
            weights = [p.attract_at(day + self.offset, self.sim.days, market.rising_growth) * market.position_decay ** (pos - 1)
                       for p, pos in candidates]
            product = rng.choices([p for p, _ in candidates], weights)[0]
            ctx.page_type, ctx.product, ctx.quantity = "product", product, None
            ctx.viewed.append(product)
            moment("page_load")
            moment("product_detail_load")
        elif action == "add_to_cart":
            # 每個看過的商品，依它真實的轉換傾向決定要不要加購
            for product in dict.fromkeys(ctx.viewed):
                if rng.random() >= min(0.95, market.add_to_cart_base * product.conversion):
                    continue
                ctx.product, ctx.quantity = product, rng.randint(1, 3)
                moment("add_to_cart_click")
                in_cart = sum(q for p, q in ctx.cart if p is product)
                in_stock = self.stock[product.id] - in_cart >= ctx.quantity
                if in_stock and rng.random() < behavior.add_to_cart_api_success:
                    ctx.cart.append((product, ctx.quantity))
                    moment("add_to_cart_success")
        elif action == "checkout":
            ctx.page_type, ctx.product = "checkout", None
            moment("page_load")
            moment("checkout_load")
        elif action == "purchase":
            ctx.order_id = f"o{rng.getrandbits(40):010x}"
            ctx.coupon_code = rng.choice(behavior.coupon_codes) if rng.random() < behavior.coupon_rate else None
            backend_order()
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

    # ---------- 輸出 ----------

    def product_rows(self) -> list[dict[str, Any]]:
        """商品目錄（業務資料）：公開的屬性，不含真相。上架日以原本期間的開始日計，不受情境影響。"""
        first_day = self.sim.start_date
        return [
            {
                "product_id": p.id,
                "name": p.name,
                "category": p.category,
                "price": p.price,
                "cost": p.cost,
                "launch_date": (first_day + timedelta(days=p.launch_day)).isoformat()
                if p.launch_day else (first_day - timedelta(days=30 + p.legacy_rank * 3)).isoformat(),
                "legacy_rank": p.legacy_rank,
            }
            for p in self.products
        ]

    def _truth(self) -> list[dict[str, Any]]:
        """真正的潛力商品：真實的「每次商品頁瀏覽預期毛利」（轉換傾向 × 每件毛利）在前 25%，但實際曝光在後 50%。

        和選品的目標（賺錢）一致：不只是好賣，而是每多一次曝光、多一次瀏覽，最能多賺。
        """
        n = len(self.products)
        value = {p.id: p.conversion * (p.price - p.cost) for p in self.products}
        by_value = sorted(self.products, key=lambda p: value[p.id], reverse=True)
        top_value = {p.id for p in by_value[: max(1, n // 4)]}
        by_exposure = sorted(self.products, key=lambda p: (self.impressions[p.id], p.id))
        low_exposure = {p.id for p in by_exposure[: n // 2]}
        return [
            {
                "product_id": p.id,
                "attractiveness": round(p.attractiveness, 4),
                "conversion": round(p.conversion, 4),
                "value_per_view": round(value[p.id], 2),
                "rising": p.rising,
                "launch_day": p.launch_day,
                "legacy_rank": p.legacy_rank,
                "impressions": self.impressions[p.id],
                "is_potential": p.id in top_value and p.id in low_exposure,
            }
            for p in self.products
        ]


def catalog(project: Project) -> tuple[list[dict[str, Any]], list[str]]:
    """商品目錄與首頁精選（不跑模擬，所以很快）；demo 商店和模擬器看到的是同一份。"""
    generator = _Generator(project)
    featured = [p.id for p in generator.featured(generator.sim.days)]
    return generator.product_rows(), featured


def simulate(project: Project, scenario: Scenario | None = None) -> World:
    """依 simulation.seed 產生確定性的模擬世界；給 scenario 時模擬那個情境（同一個商品世界）。"""
    return _Generator(project, scenario).run()


def generate_events(project: Project) -> list[dict[str, Any]]:
    """只要前端事件時用。"""
    return simulate(project).events
