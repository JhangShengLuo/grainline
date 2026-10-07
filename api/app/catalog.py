"""demo 商店的商品：id 與價格和合成資料相同，名稱與分類是固定的假名稱。"""

from __future__ import annotations

from typing import Any

from .generator import product_catalog
from .spec import Project

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


def catalog_items(project: Project) -> list[dict[str, Any]]:
    items = []
    for i, (product_id, price) in enumerate(product_catalog(project)):
        noun, category = _NOUNS[i % len(_NOUNS)]
        items.append({
            "id": product_id,
            "name": f"{_ADJECTIVES[(i // len(_NOUNS)) % len(_ADJECTIVES)]}{noun}",
            "category": category,
            "price": price,
        })
    return items
