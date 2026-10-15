"""模擬年末檔期的電商：顧客跨多個 session、從不同活動進站、逛列表、看商品、加購、結帳。只產生假資料。

行為和埋點分開：Shopper 決定「顧客做了什麼」，寫成 hit 時只帶埋點規格裡有的參數。
所以同一個 seed、換一份埋點規格，顧客的行為和後端訂單完全一樣，只有 hit 的長相不同。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from .hits import encode
from .tracking import TrackingPlan

# 活動代碼 → (utm_source, utm_medium)
CAMPAIGNS = {
    "xmas_fb": ("fb", "paid_social"),
    "xmas_line": ("line", "message"),
    "xmas_gg": ("google", "cpc"),
    "xmas_edm": ("edm", "email"),
}
# 第一次進站多半是社群廣告帶來的；之後回訪多半是搜尋廣告和 EDM 帶回來的
FIRST_VISIT = {"xmas_fb": 0.55, "xmas_line": 0.33, "xmas_gg": 0.07, "xmas_edm": 0.05}
RETURN_VISIT = {"xmas_fb": 0.08, "xmas_line": 0.05, "xmas_gg": 0.45, "xmas_edm": 0.42}

CATALOG = {
    "耳機": (["降噪耳機", "運動耳機", "頭戴耳機", "兒童耳機"], (990, 6990)),
    "吸塵器": (["無線吸塵器", "掃地機器人", "手持吸塵器", "除蟎機"], (2990, 15990)),
    "保溫瓶": (["隨行杯", "燜燒罐", "保溫壺", "冰霸杯"], (390, 1290)),
    "外套": (["羽絨外套", "刷毛外套", "防風外套", "鋪棉背心"], (1290, 4990)),
    "保養": (["聖誕禮盒", "護手霜", "精華液", "面膜組"], (290, 2490)),
    "配件": (["行動電源", "充電線", "手機殼", "收納包"], (190, 1290)),
}
LIST_SIZE = 6


@dataclass
class SimConfig:
    seed: int = 7
    visitors: int = 1500
    days: int = 14
    start: date = date(2026, 12, 1)
    member_rate: float = 0.6


@dataclass(eq=False)
class Product:
    sku: str
    name: str
    cat: str
    price: float
    cost: float
    appeal: float   # 真相：卡片被點的吸引力（報表看不到）
    conv: float     # 真相：看了商品頁之後加購的傾向

    def item(self, pos: int) -> dict[str, Any]:
        return {"sku": self.sku, "cat": self.cat, "price": int(self.price), "pos": pos}


@dataclass
class World:
    hits: list[dict[str, Any]]           # {hit_id, ts, url, source}
    orders: list[dict[str, Any]]
    order_lines: list[dict[str, Any]]
    products: list[Product]


def _lognormal(rng: random.Random, sigma: float) -> float:
    return math.exp(rng.gauss(-sigma * sigma / 2, sigma))


def make_products(seed: int) -> list[Product]:
    rng = random.Random(seed)
    products = []
    for c, (cat, (names, (low, high))) in enumerate(CATALOG.items()):
        for i, name in enumerate(names):
            price = float(round(rng.uniform(low, high), -1))
            products.append(Product(
                sku=f"{'EHBJSA'[c]}{i + 1:02d}",
                name=name,
                cat=cat,
                price=price,
                cost=float(round(price * rng.uniform(0.45, 0.7))),
                appeal=_lognormal(rng, 0.5),
                conv=_lognormal(rng, 0.5),
            ))
    # 刻意放幾個「被看很多、轉換很差」的商品：品類漏斗要找的就是它們
    for product in (products[1], products[9], products[14]):
        product.appeal, product.conv = 2.6, 0.25
    return products


class Recorder:
    """把顧客的動作寫成 hit（只帶埋點規格裡有的參數），後端訂單另外記。"""

    def __init__(self, plan: TrackingPlan, source: str = "sim") -> None:
        self.plan = plan
        self.source = source
        self.hits: list[dict[str, Any]] = []
        self.orders: list[dict[str, Any]] = []
        self.order_lines: list[dict[str, Any]] = []

    def hit(self, shopper: Shopper, ev: str, at: datetime, **params: Any) -> None:
        spec = self.plan.events.get(ev)
        wanted = spec.params if spec else []
        query = {"ev": ev, "aid": shopper.aid, "uid": shopper.uid if shopper.logged_in else None,
                 "sid": shopper.sid, "ts": at.isoformat(timespec="seconds")}
        query |= {key: value for key, value in params.items() if key in wanted}
        self.hits.append({"ts": query["ts"], "url": encode(query), "source": self.source})

    def order(self, shopper: Shopper, order_id: str, at: datetime) -> float:
        total = 0.0
        for line_no, (product, qty) in enumerate(shopper.cart, start=1):
            self.order_lines.append({"order_id": order_id, "line_no": line_no, "sku": product.sku, "qty": qty,
                                     "unit_price": product.price, "line_revenue": product.price * qty})
            total += product.price * qty
        self.orders.append({
            "order_id": order_id,
            # 後端只知道會員編號；訪客結帳時沒有 uid，也不知道瀏覽器的 aid
            "uid": shopper.uid if shopper.logged_in else None,
            "created_at": at.isoformat(timespec="seconds"),
            "total": total,
            "item_count": sum(q for _, q in shopper.cart),
        })
        return total


@dataclass
class Shopper:
    """一位顧客（一個瀏覽器）。每個動作會寫出 hit，並讓時間往前走。"""

    aid: str
    uid: str | None
    recorder: Recorder
    rng: random.Random
    logged_in: bool = False
    sid: str = ""
    clock: datetime = datetime(2026, 12, 1)
    cart: list[tuple[Product, int]] = field(default_factory=list)
    shown: list[Product] = field(default_factory=list)

    def _tick(self) -> datetime:
        at = self.clock
        self.clock += timedelta(seconds=self.rng.randint(8, 90))
        return at

    def land(self, sid: str, at: datetime, campaign: str | None) -> None:
        """進站：從活動廣告進來的話，第一頁的 URL 帶 utm。"""
        self.sid, self.clock, self.cart, self.shown = sid, at, [], []
        source, medium = CAMPAIGNS[campaign] if campaign else (None, None)
        page = "/campaign/xmas" if campaign else "/"
        self.recorder.hit(self, "page_view", self._tick(), page=page,
                          utm_source=source, utm_medium=medium, utm_campaign=campaign)

    def browse(self, list_name: str, page: str, products: list[Product]) -> None:
        self.shown = products
        self.recorder.hit(self, "list_view", self._tick(), page=page, list=list_name,
                          items=[p.item(pos) for pos, p in enumerate(products, start=1)])

    def view(self, product: Product) -> None:
        page = f"/p/{product.sku}"
        self.recorder.hit(self, "page_view", self._tick(), page=page)
        self.recorder.hit(self, "product_view", self._tick(), page=page, sku=product.sku, cat=product.cat,
                          price=product.price)

    def add(self, product: Product, qty: int) -> None:
        self.cart.append((product, qty))
        self.recorder.hit(self, "add_to_cart", self._tick(), sku=product.sku, qty=qty, price=product.price)

    def purchase(self, order_id: str) -> None:
        if self.uid:
            self.logged_in = True  # 會員結帳前要登入，之後的 hit 都帶 uid
        self.recorder.hit(self, "page_view", self._tick(), page="/checkout")
        at = self._tick()
        total = self.recorder.order(self, order_id, at)
        self.recorder.hit(self, "purchase", at, order=order_id, value=total)
        self.cart = []


def _pick(rng: random.Random, weights: dict[str, float]) -> str:
    return rng.choices(list(weights), list(weights.values()))[0]


def simulate(plan: TrackingPlan, config: SimConfig | None = None) -> World:
    config = config or SimConfig()
    rng = random.Random(config.seed)
    products = make_products(config.seed)
    by_cat: dict[str, list[Product]] = {}
    for p in products:
        by_cat.setdefault(p.cat, []).append(p)
    recorder = Recorder(plan)
    start = datetime.combine(config.start, datetime.min.time())
    order_seq = 0

    for i in range(config.visitors):
        shopper = Shopper(aid=f"a{rng.getrandbits(32):08x}",
                          uid=f"u{i:05d}" if rng.random() < config.member_rate else None,
                          recorder=recorder, rng=rng)
        visits = rng.choices([1, 2, 3, 4], [0.45, 0.30, 0.15, 0.10])[0]
        starts = sorted(start + timedelta(days=rng.uniform(0, config.days - 0.1)) for _ in range(visits))
        for k, at in enumerate(starts):
            campaign = None
            if rng.random() < (0.6 if k == 0 else 0.55):
                campaign = _pick(rng, FIRST_VISIT if k == 0 else RETURN_VISIT)
            shopper.land(f"s{rng.getrandbits(32):08x}", at, campaign)
            if campaign:
                pool = sorted(products, key=lambda p: -p.appeal * rng.random())[:LIST_SIZE]
                shopper.browse("xmas_picks", "/campaign/xmas", pool)
            else:
                cat = rng.choice(list(by_cat))
                pool = sorted(by_cat[cat], key=lambda p: -p.appeal * rng.random())
                shopper.browse(cat, f"/c/{cat}", pool[:LIST_SIZE])
            # 點哪幾個商品：看吸引力，也看位置
            weights = [p.appeal * 0.85 ** pos for pos, p in enumerate(shopper.shown)]
            viewed = list(dict.fromkeys(rng.choices(shopper.shown, weights, k=rng.randint(1, 3))))
            for product in viewed:
                shopper.view(product)
                if rng.random() < min(0.9, 0.3 * product.conv):
                    shopper.add(product, rng.choices([1, 2], [0.8, 0.2])[0])
            # 回訪越多次越可能買：最後一次進站的活動常常只是「收割」
            if shopper.cart and rng.random() < min(0.7, 0.12 + 0.2 * k):
                order_seq += 1
                shopper.purchase(f"o{order_seq:05d}")

    hits = sorted(recorder.hits, key=lambda h: h["ts"])
    for hit_id, hit in enumerate(hits, start=1):
        hit["hit_id"] = hit_id
    return World(hits=hits, orders=recorder.orders, order_lines=recorder.order_lines, products=products)


LIVE_PERSON = "live-you"


class LiveShopper:
    """demo 上「你自己逛」的那位顧客：動作由畫面上的點擊決定，hit 和訂單的寫法和模擬器完全一樣。"""

    def __init__(self, plan: TrackingPlan, config: SimConfig) -> None:
        self.products = {p.sku: p for p in make_products(config.seed)}
        self.recorder = Recorder(plan, source="live")
        start = datetime.combine(config.start, datetime.min.time()) + timedelta(days=config.days - 1, hours=20)
        self.shopper = Shopper(aid=LIVE_PERSON, uid=None, recorder=self.recorder, rng=random.Random(config.seed), clock=start)
        self.sessions = 0
        self.orders = 0
        self.flushed = (0, 0, 0)  # 已寫進倉儲的 hits、orders、order_lines 數

    def act(self, action: str, campaign: str | None = None, sku: str | None = None) -> None:
        s = self.shopper
        if action != "land" and not self.sessions:
            raise ValueError("先選一個進站來源")
        if action == "land":
            if campaign is not None and campaign not in CAMPAIGNS:
                raise ValueError(f"沒有 {campaign} 這個活動")
            self.sessions += 1
            at = s.clock + timedelta(hours=1) if self.sessions > 1 else s.clock
            s.land(f"slive{self.sessions:02d}", at, campaign)
            self._browse(campaign)
        elif action == "list":
            self._browse(None)
        elif action in ("product", "add"):
            if sku not in self.products:
                raise ValueError(f"沒有 {sku} 這個商品")
            if action == "product":
                s.view(self.products[sku])
            else:
                s.add(self.products[sku], 1)
        elif action == "purchase":
            if not s.cart:
                raise ValueError("購物車是空的")
            self.orders += 1
            s.purchase(f"live-{self.orders:03d}")
        else:
            raise ValueError(f"不支援 {action}")

    def _browse(self, campaign: str | None) -> None:
        ranked = sorted(self.products.values(), key=lambda p: -p.appeal)
        self.shopper.browse("xmas_picks" if campaign or self.sessions else "home", "/campaign/xmas", ranked[:LIST_SIZE])

    def pending(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        """還沒寫進倉儲的新資料。"""
        h, o, l = self.flushed
        r = self.recorder
        self.flushed = (len(r.hits), len(r.orders), len(r.order_lines))
        return r.hits[h:], r.orders[o:], r.order_lines[l:]
