"""一筆埋點 hit 和它的 URL：前端真的送出去的長相是 /collect?ev=…&aid=…&items=[…]。"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote, unquote

ENDPOINT = "/collect"


def _text(value: Any) -> str:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def encode(params: dict[str, Any]) -> str:
    """依參數的順序組成 URL；None 的參數不送。值一律跳脫，所以中文、&、= 都安全。"""
    query = "&".join(f"{key}={quote(_text(value), safe='')}" for key, value in params.items() if value is not None)
    return f"{ENDPOINT}?{query}"


def decode(url: str) -> dict[str, str]:
    """URL → 參數（值都是字串，和 DuckDB 解析出來的一樣）。"""
    query = url.split("?", 1)[1] if "?" in url else ""
    out: dict[str, str] = {}
    for pair in query.split("&"):
        if pair:
            key, _, value = pair.partition("=")
            out[key] = unquote(value)
    return out
