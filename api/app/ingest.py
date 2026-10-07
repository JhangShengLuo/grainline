"""接收 demo 商店送來的事件。

原始事件不可變、schema-on-read：這裡只驗證信封格式，和 L1 不符的地方回傳 warning，
事件照樣保存，由 L2 決定隔離（未宣告的事件）或轉型（型別不符的值會變成 NULL）。
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .spec import Property, TrackingPlan

LOCAL_TZ = ZoneInfo("Asia/Taipei")  # L1 約定：timestamp 一律是台北時間、不帶時區


class IncomingEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=64)
    event_name: str = Field(min_length=1, max_length=64)
    timestamp: datetime
    anonymous_id: str = Field(min_length=1, max_length=64)
    user_id: str | None = Field(None, max_length=64)
    tracking_plan_version: int
    properties: dict[str, Any] = {}

    @field_validator("timestamp")
    @classmethod
    def _to_local(cls, value: datetime) -> datetime:
        if value.tzinfo is not None:
            value = value.astimezone(LOCAL_TZ).replace(tzinfo=None)
        return value.replace(microsecond=0)

    def to_raw(self) -> dict[str, Any]:
        data = self.model_dump()
        data["timestamp"] = self.timestamp.isoformat(sep=" ")
        return data


class IngestBatch(BaseModel):
    events: list[IncomingEvent] = Field(min_length=1, max_length=500)


def _type_ok(prop: Property, value: Any) -> bool:
    match prop.type:
        case "string":
            return isinstance(value, str)
        case "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        case "number":
            return isinstance(value, int | float) and not isinstance(value, bool)
    return isinstance(value, bool)


def plan_warnings(plan: TrackingPlan, event: IncomingEvent) -> list[str]:
    """這個事件和目前的 L1 哪裡不一致。空 list 表示完全符合。"""
    messages: list[str] = []
    if event.tracking_plan_version != plan.version:
        messages.append(f"SDK 依據的是 tracking plan v{event.tracking_plan_version}，目前是 v{plan.version}")
    if event.event_name not in plan.events:
        messages.append(f"事件 {event.event_name} 沒有在 L1 宣告：會進 stg_unknown_events，不會流到報表")
        return messages
    declared = plan.properties_of(event.event_name)
    for name in sorted(event.properties.keys() - declared.keys()):
        messages.append(f"property {name} 沒有在 L1 宣告：下游拿不到")
    for name, prop in declared.items():
        if name not in event.properties or event.properties[name] is None:
            if prop.required:
                messages.append(f"缺少 property {name}（{prop.type}）：下游會是 NULL")
            continue
        value = event.properties[name]
        if not _type_ok(prop, value):
            messages.append(f"{name} 應為 {prop.type}，收到 {value!r}：L2 會嘗試轉型，失敗則為 NULL")
        elif prop.enum and value not in prop.enum:
            messages.append(f"{name} = {value!r} 不在 L1 enum {prop.enum} 裡")
        elif prop.type in ("integer", "number"):
            if prop.min is not None and value < prop.min:
                messages.append(f"{name} = {value} 小於 L1 的下限 {prop.min:g}")
            if prop.max is not None and value > prop.max:
                messages.append(f"{name} = {value} 大於 L1 的上限 {prop.max:g}")
    return messages


class LiveStore:
    """demo 商店的事件存成 NDJSON，倉儲重建（例如改了規格）時會重新載入。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._ids: set[str] | None = None

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def append_new(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """附加尚未收過的事件（依 event_id 去重，SDK 重送是安全的），回傳實際新增的事件。"""
        with self._lock:
            if self._ids is None:
                self._ids = {e["event_id"] for e in self.read_all()}
            fresh: list[dict[str, Any]] = []
            for event in events:
                if event["event_id"] not in self._ids:
                    self._ids.add(event["event_id"])
                    fresh.append(event)
            if fresh:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as f:
                    for event in fresh:
                        f.write(json.dumps(event, ensure_ascii=False) + "\n")
            return fresh

    def clear(self) -> None:
        with self._lock:
            self.path.unlink(missing_ok=True)
            self._ids = set()
