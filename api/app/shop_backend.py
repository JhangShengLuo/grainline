"""demo 商店的後端：付款時建立訂單（計價、驗證折扣碼、算運費），寫入訂單主檔、明細與折扣碼使用紀錄。

和模擬器的後端不同，這裡沒有刻意的 bug：過期、未達門檻、超過上限的折扣碼都會被拒絕，並告訴使用者原因。
訂單存成 NDJSON，倉儲重建時一併載入；換 tracking plan 不影響後端資料。
"""

from __future__ import annotations

import json
import random
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import duckdb
from pydantic import BaseModel, ConfigDict, Field

from .generator import catalog
from .spec import Project

TABLES = ("orders", "order_lines", "coupon_redemptions")


class OrderLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: str = Field(min_length=1, max_length=32)
    quantity: int = Field(ge=1, le=99)


class ShopOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    anonymous_id: str = Field(min_length=1, max_length=64)
    user_id: str | None = Field(None, max_length=64)
    platform: Literal["web", "app"]
    lines: list[OrderLine] = Field(min_length=1, max_length=50)
    coupon_code: str | None = Field(None, max_length=32)


class LiveBackend:
    """demo 商店建立的後端資料（每一列記錄屬於哪張表）。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def read_all(self) -> dict[str, list[dict[str, Any]]]:
        tables: dict[str, list[dict[str, Any]]] = {t: [] for t in TABLES}
        if self.path.exists():
            with self.path.open(encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        item = json.loads(line)
                        tables[item["table"]].append(item["row"])
        return tables

    def append(self, rows: dict[str, list[dict[str, Any]]]) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                for table, items in rows.items():
                    for row in items:
                        f.write(json.dumps({"table": table, "row": row}, ensure_ascii=False) + "\n")

    def clear(self) -> int:
        with self._lock:
            count = sum(len(v) for v in self.read_all().values()) if self.path.exists() else 0
            self.path.unlink(missing_ok=True)
            return count


def _check_coupon(project: Project, con: duckdb.DuckDBPyConnection, code: str, subtotal: float,
                  user_key: str, now: datetime) -> tuple[Any | None, str]:
    """回傳 (折扣碼, 原因)；被拒絕時折扣碼是 None，原因是給使用者看的白話。"""
    coupon = next((c for c in project.simulation.coupons if c.code == code), None)
    if coupon is None:
        return None, "沒有這個折扣碼"
    today = now.date()
    if today < coupon.valid_from:
        return None, f"這個折扣碼 {coupon.valid_from} 才開始"
    if today > coupon.valid_until:
        return None, f"這個折扣碼已在 {coupon.valid_until} 過期"
    if subtotal < coupon.min_spend:
        return None, f"未達最低消費 NT$ {coupon.min_spend:,.0f}"
    (used,) = con.execute(
        "select count(*) from src_coupon_redemptions where coupon_code = ? and status = 'applied'", [code]
    ).fetchone()
    if used >= coupon.usage_limit:
        return None, "這個折扣碼已經被用完了"
    (mine,) = con.execute(
        "select count(*) from src_coupon_redemptions where coupon_code = ? and status = 'applied' "
        "and coalesce(user_id, anonymous_id) = ?", [code, user_key]
    ).fetchone()
    if mine >= coupon.per_user_limit:
        return None, f"每人限用 {coupon.per_user_limit} 次，你已經用過了"
    return coupon, f"已套用 {coupon.campaign}"


def create_order(project: Project, con: duckdb.DuckDBPyConnection, order: ShopOrder,
                 now: datetime) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    """回傳 (給商店的訂單摘要, 要寫入後端的資料列)。"""
    rows, _ = catalog(project)
    products = {r["product_id"]: r for r in rows}
    unknown = [line.product_id for line in order.lines if line.product_id not in products]
    if unknown:
        raise ValueError(f"沒有這些商品：{', '.join(unknown)}")
    behavior = project.simulation.behavior
    subtotal = round(sum(products[li.product_id]["price"] * li.quantity for li in order.lines), 2)
    user_key = order.user_id or order.anonymous_id
    coupon, reason = (None, "")
    if order.coupon_code:
        coupon, reason = _check_coupon(project, con, order.coupon_code.strip().upper(), subtotal, user_key, now)
    discount = 0.0
    if coupon is not None:
        discount = float(round(subtotal * coupon.value / 100)) if coupon.discount_type == "percent" \
            else float(min(coupon.value, subtotal))
    shipping = 0.0 if subtotal >= behavior.free_shipping_over else behavior.shipping_fee
    order_id = f"o-live-{random.getrandbits(32):08x}"
    created = now.isoformat(sep=" ")
    record = {
        "order_id": order_id,
        "anonymous_id": order.anonymous_id,
        "user_id": order.user_id,
        "platform": order.platform,
        "status": "paid",
        "item_count": sum(li.quantity for li in order.lines),
        "subtotal": subtotal,
        "discount_amount": discount,
        "shipping_fee": shipping,
        "total": round(subtotal - discount + shipping, 2),
        "coupon_code": coupon.code if coupon else None,
        "created_at": created,
        "cancelled_at": None,
        "refunded_at": None,
    }
    lines = [
        {
            "order_id": order_id,
            "line_no": i,
            "product_id": li.product_id,
            "quantity": li.quantity,
            "unit_price": products[li.product_id]["price"],
            "line_revenue": products[li.product_id]["price"] * li.quantity,
            "coupon_code": record["coupon_code"],
            "ordered_at": created,
            "anonymous_id": order.anonymous_id,
            "user_id": order.user_id,
        }
        for i, li in enumerate(order.lines, start=1)
    ]
    redemptions = [] if coupon is None else [{
        "redemption_id": f"r-live-{random.getrandbits(32):08x}",
        "coupon_code": coupon.code,
        "order_id": order_id,
        "user_id": order.user_id,
        "anonymous_id": order.anonymous_id,
        "redeemed_at": created,
        "discount_amount": discount,
        "status": "applied",
        "reversed_at": None,
    }]
    summary = {
        "order_id": order_id,
        "subtotal": subtotal,
        "discount_amount": discount,
        "shipping_fee": shipping,
        "total": record["total"],
        "item_count": record["item_count"],
        "created_at": created,
        "coupon": None if not order.coupon_code else {
            "code": order.coupon_code.strip().upper(), "accepted": coupon is not None, "message": reason,
        },
    }
    return summary, {"orders": [record], "order_lines": lines, "coupon_redemptions": redemptions}
