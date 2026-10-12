"""AI 提案：行銷用自然語言提出決策需求，Claude 在指標庫的範圍內寫出 L5 劇本，規則引擎驗證，不通過就讓它修正。

分工：Claude 負責把需求翻成劇本、用白話解釋取捨；規則引擎負責驗證（schema、可加性、反推需求、實際試跑）。
Claude 只能用指標庫裡的指標，資料不夠時要說缺什麼，不能自己發明指標。結果要人按下儲存才會寫入。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Literal

import anthropic
import duckdb
import yaml
from pydantic import ValidationError

from .checks import effective_additivity
from .decisions import requirements, run_decision
from .library import Library, load_library
from .lineage import Lineage, resolve
from .spec import Decision, Project

OPENROUTER_BASE_URL = "https://openrouter.ai/api"  # OpenRouter 的 Anthropic 相容端點
DEFAULT_MODELS = {"anthropic": "claude-opus-5-5", "openrouter": "anthropic/claude-opus-5.5"}


@dataclass(frozen=True)
class Provider:
    """呼叫 Claude 的方式：直接用 Anthropic API，或經過 OpenRouter。"""

    name: Literal["anthropic", "openrouter"]
    client: Any
    model: str


def make_provider() -> Provider | None:
    """設了 OPENROUTER_API_KEY 就走 OpenRouter（優先，即使也有 Anthropic 金鑰）；否則用 Anthropic 認證；都沒有就回傳 None。

    走 OpenRouter 時只送 OpenRouter 的金鑰（Authorization: Bearer），不會把 Anthropic 金鑰送出去。
    """
    override = os.environ.get("GRAINLINE_MODEL")
    try:
        if key := os.environ.get("OPENROUTER_API_KEY"):
            client = anthropic.Anthropic(base_url=OPENROUTER_BASE_URL, auth_token=key, api_key=None)
            return Provider("openrouter", client, override or DEFAULT_MODELS["openrouter"])
        if any(os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE")):
            return Provider("anthropic", anthropic.Anthropic(), override or DEFAULT_MODELS["anthropic"])
    except anthropic.AnthropicError:
        return None
    return None
MAX_ATTEMPTS = 3

_FIELDS = ["feasible", "name", "label", "question", "inputs", "score", "rules", "top_k",
           "missing_metrics", "explanation", "tradeoffs"]


def _rate_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "numerator": {"type": "string", "description": "inputs 裡的 alias"},
            "denominator": {"type": "string", "description": "inputs 裡的 alias"},
            "prior_strength": {"type": "number", "description": "樣本少時往全體平均收斂的強度；0 表示不收斂"},
        },
        "required": ["numerator", "denominator", "prior_strength"],
        "additionalProperties": False,
    }


def output_schema(library: Library) -> dict[str, Any]:
    """structured outputs 的 schema：指標只能從指標庫挑。"""
    return {
        "type": "object",
        "properties": {
            "feasible": {"type": "boolean", "description": "指標庫的指標能不能回答這個問題"},
            "name": {"type": "string", "description": "劇本代號，小寫 snake_case，例如 restock_risk"},
            "label": {"type": "string", "description": "劇本名稱（繁體中文，短）"},
            "question": {"type": "string", "description": "這個劇本回答的問題（繁體中文，一句話）"},
            "inputs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "alias": {"type": "string", "description": "小寫 snake_case"},
                        "metric": {"type": "string", "enum": sorted(library.metrics)},
                    },
                    "required": ["alias", "metric"],
                    "additionalProperties": False,
                },
            },
            "score": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "rate": _rate_schema(),
                    "use_multiply": {"type": "boolean"},
                    "multiply": _rate_schema(),
                },
                "required": ["label", "rate", "use_multiply", "multiply"],
                "additionalProperties": False,
            },
            "rules": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "input": {"type": "string"},
                        "op": {"type": "string", "enum": ["gt", "gte", "lt", "lte"]},
                        "value": {"type": "string", "description": "數字（例如 20）或百分位（例如 p50）"},
                        "why": {"type": "string", "description": "白話理由"},
                    },
                    "required": ["input", "op", "value", "why"],
                    "additionalProperties": False,
                },
            },
            "top_k": {"type": "integer"},
            "missing_metrics": {
                "type": "array",
                "description": "要完整回答這個問題、但指標庫沒有的指標",
                "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "why": {"type": "string"}},
                    "required": ["name", "why"],
                    "additionalProperties": False,
                },
            },
            "explanation": {"type": "string", "description": "給行銷看的白話說明：這個劇本怎麼回答問題"},
            "tradeoffs": {"type": "array", "items": {"type": "string"}, "description": "取捨與限制"},
        },
        "required": _FIELDS,
        "additionalProperties": False,
    }


def _library_text(project: Project, library: Library) -> str:
    lines = []
    for name, m in library.metrics.items():
        is_ratio = all(n.metric is not None for n in m.needs)
        if is_ratio:
            kind = "比率（不能當 input；要用的話放在 score.rate 的分子分母）"
        elif name in project.metric_layer.metrics:
            kind = "可加，可以當 input" if effective_additivity(project, name) == "additive" else "不可加，不能當 input"
        else:
            kind = "這個專案還沒定義"
        lines.append(f"- {name}（{m.label}，{library.groups[m.group]}，{m.grain}）：{m.definition}。{kind}")
    return "\n".join(lines)


def system_prompt(project: Project, library: Library) -> str:
    example = yaml.safe_dump(
        {"potential_products": project.decisions["potential_products"].model_dump(exclude_none=True)},
        allow_unicode=True, sort_keys=False,
    ) if "potential_products" in project.decisions else ""
    return f"""你是自營品牌電商的資料策略助理。行銷會用一句話提出想做的決策，你要把它寫成 Grainline 的 L5 決策劇本。

劇本的運作方式：
- 決策對象一律是商品（entity: product_id）。
- inputs：用到的指標，每個指標在時間窗口內依商品加總，所以只能用「可加」的指標。
- score：分數 = rate（分子 ÷ 分母，可以設定 prior_strength 讓樣本少的往全體平均收斂）× multiply（選用，例如每件毛利；分母為 0 時用全體平均）。分數越高越優先。
- rules：候選條件與防呆，每條都要有白話理由；value 可以是數字，或 p50 這種「所有商品的第 50 百分位」。
- top_k：建議清單要幾個（通常 5）。
- 結果只是建議清單，不會自動動預算或版位。

只能用下面指標庫裡的指標（名稱要完全一樣）：
{_library_text(project, library)}

規則：
- 不能自己發明指標。指標庫不夠回答問題時，feasible 設 false，把缺的指標寫在 missing_metrics，並說明用現有指標能做到哪裡。
- 比率指標不能當 input；要用轉換率之類的比例，請把分子分母的可加指標放進 inputs，在 score.rate 裡相除。
- 樣本少的商品容易誤判，分母是瀏覽、曝光這類數量時，prior_strength 建議 20–50，並加上最少樣本數的規則。
- 不用 multiply 時 use_multiply 設 false，multiply 仍要填（會被忽略）。
- 所有文字用繁體中文（台灣用語），寫給不懂資料的行銷看。

目前專案裡的範例劇本：
{example}"""


def to_decision(proposal: dict[str, Any]) -> dict[str, Any]:
    """Claude 的輸出 → l5_decisions.yaml 的格式。"""
    score: dict[str, Any] = {"label": proposal["score"]["label"], "rate": proposal["score"]["rate"]}
    if proposal["score"].get("use_multiply"):
        score["multiply"] = proposal["score"]["multiply"]

    def value(v: str) -> float | str:
        try:
            return float(v)
        except ValueError:
            return v.strip()

    return {
        "label": proposal["label"],
        "question": proposal["question"],
        "entity": "product_id",
        "inputs": {i["alias"]: i["metric"] for i in proposal["inputs"]},
        "score": score,
        "rules": [{**r, "value": value(r["value"])} for r in proposal["rules"]],
        "top_k": proposal["top_k"],
    }


def validate(project: Project, con: duckdb.DuckDBPyConnection, name: str, decision: dict[str, Any]) -> tuple[list[str], dict[str, Any] | None]:
    """用規則引擎驗證：schema、反推需求、實際試跑。回傳 (問題清單, 試跑結果)。"""
    try:
        parsed = Decision.model_validate(decision)
    except ValidationError as exc:
        return [f"格式錯誤：{e['msg']}（{'.'.join(map(str, e['loc']))}）" for e in exc.errors()], None
    candidate = project.model_copy(update={"decisions": {**project.decisions, name: parsed}})
    lineage: Lineage = resolve(candidate)
    req = requirements(candidate, lineage, name)
    problems = [p for i in req["inputs"] for p in i.get("problems", [])]
    if problems:
        return problems, None
    result = run_decision(candidate, lineage, con, name)
    if result["status"] == "ready" and not result["candidates"]:
        return ["試跑沒有任何商品有資料：請檢查 inputs"], result
    return [], result


def _create(provider: Provider, **request: Any) -> Any:
    if provider.name == "anthropic":
        # 被安全分類器拒絕時，伺服器端改用適合的模型重跑（Anthropic API 專用的 beta）
        return provider.client.beta.messages.create(
            **request, betas=["server-side-fallback-2026-07-01"], fallbacks="default"
        )
    # OpenRouter：只送標準參數，Anthropic 專用的 beta 參數不送
    return provider.client.messages.create(**request)


def propose_playbook(provider: Provider, project: Project, con: duckdb.DuckDBPyConnection, request: str) -> dict[str, Any]:
    library = load_library()
    system = system_prompt(project, library)
    schema = output_schema(library)
    messages: list[dict[str, Any]] = [{"role": "user", "content": request}]
    attempts: list[dict[str, Any]] = []

    for attempt in range(1, MAX_ATTEMPTS + 1):
        response = _create(
            provider,
            model=provider.model,
            max_tokens=16000,
            # 系統提示（指標庫、範例）每次都一樣，標記快取
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=messages,
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
        )
        if response.stop_reason == "refusal":
            return {"status": "refused", "attempts": attempts,
                    "message": "Claude 拒絕了這個請求，請換個說法再試一次。"}
        if response.stop_reason == "max_tokens":
            return {"status": "error", "attempts": attempts, "message": "回答太長被截斷，請把需求寫得更具體。"}
        text = next((b.text for b in response.content if b.type == "text"), "")
        proposal = json.loads(text)
        if not proposal["feasible"]:
            return {"status": "infeasible", "attempts": attempts + [{"attempt": attempt, "problems": []}],
                    "proposal": proposal}
        name = proposal["name"] if re.fullmatch(r"[a-z][a-z0-9_]*", proposal["name"]) else "ai_playbook"
        while name in project.decisions:
            name = f"{name}_new"
        decision = to_decision(proposal)
        problems, preview = validate(project, con, name, decision)
        attempts.append({"attempt": attempt, "problems": problems})
        if not problems:
            return {
                "status": "ready",
                "name": name,
                "decision": decision,
                "yaml": yaml.safe_dump({name: decision}, allow_unicode=True, sort_keys=False),
                "proposal": proposal,
                "attempts": attempts,
                "preview": preview,
            }
        # 把引擎的驗證結果交回給 Claude 修正（對話只往後加，不改前面的內容）
        messages += [
            {"role": "assistant", "content": response.content},
            {"role": "user", "content": "規則引擎驗證沒有通過，請修正後重新輸出完整的劇本：\n- " + "\n- ".join(problems)},
        ]
    return {"status": "invalid", "attempts": attempts, "message": f"試了 {MAX_ATTEMPTS} 次仍然沒有通過驗證。"}


def save_playbook(spec_path: Any, name: str, decision: dict[str, Any], project: Project) -> None:
    """附加到 l5_decisions.yaml 的最後（保留原本的註解）。"""
    if name in project.decisions:
        raise FileExistsError(f"劇本 {name} 已經存在")
    Decision.model_validate(decision)
    text = spec_path.read_text(encoding="utf-8") if spec_path.exists() else ""
    block = "\n# 由 AI 提案、經規則引擎驗證後儲存\n" + yaml.safe_dump({name: decision}, allow_unicode=True, sort_keys=False)
    spec_path.write_text(text.rstrip("\n") + "\n" + block, encoding="utf-8")
