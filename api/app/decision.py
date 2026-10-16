"""定案卡：會議結束時大家簽字的那一張。寫清楚做哪些數字、用什麼規則、前端要補什麼。"""

from __future__ import annotations

from datetime import date
from typing import Any

from .questions import Question
from .tracking import TrackingPlan

MODEL_TEXT = {"last": "最後點擊", "first": "首次點擊", "linear": "線性"}


def _money(value: float) -> str:
    return f"NT$ {value:,.0f}"


def decision_card(question: Question, plan: TrackingPlan, attribution: str | None, fanout: dict[str, Any],
                  missing: list[dict[str, Any]], today: date | None = None) -> str:
    lines = [
        f"# 定案：{question.title}",
        "",
        f"- 問題：{question.ask}",
        f"- 埋點版本：v{plan.version}（{plan.name}）",
        f"- 報表粒度：{question.grain}",
    ]
    if question.attribution:
        lines.append(f"- 歸因方式：{MODEL_TEXT[attribution or question.attribution.default]}"
                     f"（回溯 30 天；沒有活動觸點的訂單歸給「直接進站」）")
    metric = fanout["metric"]
    lines += [
        "",
        "## 不能這樣算",
        f"- {fanout['label']}：{metric} 會變成 {fanout['ratio']:.1f} 倍"
        f"（{fanout['wrong'][metric]:,.0f}，正確是 {fanout['right'][metric]:,.0f}）",
        "",
        "```sql",
        "-- 錯",
        fanout["wrong_sql"],
        "-- 對",
        fanout["right_sql"],
        "```",
        "",
        "## 前端待辦",
    ]
    todo = [m for m in missing if m["status"] != "ok" and not m["in_plan"]]
    if not todo:
        lines.append("- 無：現行埋點已經足夠")
    for m in todo:
        line = f"- 在 `{m['event']}` 事件補上 `{m['param']}`：{m['why']}"
        if m["impact"]:
            line += f"（目前 {_money(m['impact']['value'])}，占 {m['impact']['share']:.0%} {m['impact']['label']}）"
        lines.append(line)
    notes = [m for m in missing if m["status"] == "partial" and m["in_plan"]]
    if notes:
        lines += ["", "## 已確認的資料特性"]
        lines += [f"- `{m['event']}.{m['param']}` 只出現在 {m['rate']:.0%} 的 hit：{m['why']}" for m in notes]
    lines += ["", f"日期：{(today or date.today()).isoformat()}", "簽核：行銷＿＿＿　BI＿＿＿　DE＿＿＿　前端＿＿＿", ""]
    return "\n".join(lines)
