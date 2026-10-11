"""demo 商店的商品：和模擬器同一份商品目錄（同一個 seed），首頁精選也一樣。"""

from __future__ import annotations

from typing import Any

from .generator import catalog
from .spec import Project


def catalog_items(project: Project) -> list[dict[str, Any]]:
    rows, featured = catalog(project)
    rank = {pid: i for i, pid in enumerate(featured, start=1)}
    return [
        {
            "id": r["product_id"],
            "name": r["name"],
            "category": r["category"],
            "price": r["price"],
            "launch_date": r["launch_date"],
            "featured_rank": rank.get(r["product_id"]),
            "legacy_rank": r["legacy_rank"],
        }
        for r in sorted(rows, key=lambda r: r["legacy_rank"])
    ]
