"""埋點規格：每個事件帶哪些參數。模擬器照這份規格輸出 hit，所以規格沒寫的參數就真的沒有。"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

# 每個 hit 都有的參數（uid 只在登入後才有）
BASE_PARAMS = ["ev", "aid", "uid", "sid", "ts"]


class EventSpec(BaseModel):
    params: list[str]
    when: str


class TrackingPlan(BaseModel):
    version: int
    name: str
    events: dict[str, EventSpec]

    def has(self, event: str, param: str) -> bool:
        return param in BASE_PARAMS or (event in self.events and param in self.events[event].params)


def load_plans(directory: Path) -> dict[int, TrackingPlan]:
    plans = {}
    for path in sorted(directory.glob("*.yaml")):
        plan = TrackingPlan.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        plans[plan.version] = plan
    return plans
