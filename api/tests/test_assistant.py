"""M10：AI 提案（用假的 Claude client 測試整個流程，不呼叫真的 API）。"""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from app.assistant import OPENROUTER_BASE_URL, Provider, make_provider, output_schema, system_prompt, to_decision
from app.library import load_library
from app.main import create_app

from .conftest import copy_small_specs, load_small


def _proposal(**overrides: Any) -> dict[str, Any]:
    base = {
        "feasible": True,
        "name": "restock_priority",
        "label": "優先補貨",
        "question": "哪些商品賣得好、但常缺貨，應該優先補貨？",
        "inputs": [
            {"alias": "units", "metric": "units_ordered"},
            {"alias": "views", "metric": "product_detail_views"},
            {"alias": "stockout_days", "metric": "stockout_days"},
        ],
        "score": {
            "label": "每次瀏覽賣出幾件",
            "rate": {"numerator": "units", "denominator": "views", "prior_strength": 30},
            "use_multiply": False,
            "multiply": {"numerator": "units", "denominator": "views", "prior_strength": 0},
        },
        "rules": [
            {"input": "stockout_days", "op": "gte", "value": "1", "why": "最近缺過貨"},
            {"input": "views", "op": "gte", "value": "p25", "why": "有一定的瀏覽量"},
        ],
        "top_k": 5,
        "missing_metrics": [],
        "explanation": "找出轉換好、但缺過貨的商品。",
        "tradeoffs": ["沒有考慮補貨成本"],
    }
    return {**base, **overrides}


class FakeClaude:
    """依序回傳準備好的回答，並記下每次的請求。"""

    def __init__(self, *answers: dict[str, Any], stop_reason: str = "end_turn") -> None:
        self.answers = list(answers)
        self.stop_reason = stop_reason
        self.calls: list[dict[str, Any]] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        answer = self.answers.pop(0)
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            content=[SimpleNamespace(type="text", text=json.dumps(answer, ensure_ascii=False))],
        )


@pytest.fixture
def spec_dir(tmp_path: Path) -> Path:
    return copy_small_specs(tmp_path / "shop")


def _client(spec_dir: Path, tmp_path: Path, fake: Any, provider: str = "anthropic") -> TestClient:
    model = "claude-opus-5-5" if provider == "anthropic" else "anthropic/claude-opus-5.5"
    return TestClient(create_app(spec_dir, tmp_path / "data", llm=Provider(provider, fake, model)))  # type: ignore[arg-type]


def test_schema_only_allows_library_metrics() -> None:
    schema = output_schema(load_library())
    metric = schema["properties"]["inputs"]["items"]["properties"]["metric"]
    assert set(metric["enum"]) == set(load_library().metrics)
    assert schema["additionalProperties"] is False


def test_system_prompt_marks_ratios_and_additivity() -> None:
    prompt = system_prompt(load_small(), load_library())
    assert "unit_session_percentage" in prompt and "比率（不能當 input" in prompt
    assert "potential_products" in prompt  # 範例劇本


def test_to_decision_converts_numbers_and_percentiles() -> None:
    decision = to_decision(_proposal())
    assert decision["rules"][0]["value"] == 1.0 and decision["rules"][1]["value"] == "p25"
    assert "multiply" not in decision["score"]


def test_valid_proposal_is_previewed_not_saved(spec_dir: Path, tmp_path: Path) -> None:
    fake = FakeClaude(_proposal())
    body = _client(spec_dir, tmp_path, fake).post("/assistant/playbooks", json={"request": "哪些商品要優先補貨？"}).json()
    assert body["status"] == "ready" and body["name"] == "restock_priority"
    assert body["preview"]["status"] == "ready"
    assert len(body["attempts"]) == 1
    assert "restock_priority" not in (spec_dir / "l5_decisions.yaml").read_text(encoding="utf-8")
    call = fake.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["fallbacks"] == "default"
    assert call["betas"] == ["server-side-fallback-2026-07-01"]


def test_engine_feedback_lets_claude_fix_the_playbook(spec_dir: Path, tmp_path: Path) -> None:
    # 第一次把不可加的比率當 input，引擎擋下；第二次修正
    bad = _proposal(inputs=[{"alias": "units", "metric": "units_ordered"},
                            {"alias": "views", "metric": "unit_session_percentage"},
                            {"alias": "stockout_days", "metric": "stockout_days"}])
    fake = FakeClaude(bad, _proposal())
    body = _client(spec_dir, tmp_path, fake).post("/assistant/playbooks", json={"request": "優先補貨的商品"}).json()
    assert body["status"] == "ready"
    assert [bool(a["problems"]) for a in body["attempts"]] == [True, False]
    assert "不可加" in body["attempts"][0]["problems"][0]
    feedback = fake.calls[1]["messages"][-1]["content"]
    assert "規則引擎驗證沒有通過" in feedback


def test_gives_up_after_three_attempts(spec_dir: Path, tmp_path: Path) -> None:
    bad = _proposal(rules=[{"input": "returns", "op": "lt", "value": "3", "why": "x"}])
    body = _client(spec_dir, tmp_path, FakeClaude(bad, bad, bad)).post(
        "/assistant/playbooks", json={"request": "優先補貨的商品"}).json()
    assert body["status"] == "invalid" and len(body["attempts"]) == 3


def test_infeasible_requests_say_what_is_missing(spec_dir: Path, tmp_path: Path) -> None:
    answer = _proposal(feasible=False, missing_metrics=[{"name": "return_rate", "why": "需要退貨資料"}])
    body = _client(spec_dir, tmp_path, FakeClaude(answer)).post(
        "/assistant/playbooks", json={"request": "哪些商品退貨率太高？"}).json()
    assert body["status"] == "infeasible"
    assert body["proposal"]["missing_metrics"][0]["name"] == "return_rate"


def test_refusal_is_reported(spec_dir: Path, tmp_path: Path) -> None:
    body = _client(spec_dir, tmp_path, FakeClaude(_proposal(), stop_reason="refusal")).post(
        "/assistant/playbooks", json={"request": "哪些商品要優先補貨？"}).json()
    assert body["status"] == "refused"


def test_save_appends_and_keeps_comments(spec_dir: Path, tmp_path: Path) -> None:
    client = _client(spec_dir, tmp_path, FakeClaude(_proposal()))
    proposal = client.post("/assistant/playbooks", json={"request": "優先補貨"}).json()
    saved = client.post("/assistant/playbooks/save", json={"name": proposal["name"], "decision": proposal["decision"]})
    assert saved.status_code == 200
    text = (spec_dir / "l5_decisions.yaml").read_text(encoding="utf-8")
    assert text.startswith("# L5 決策劇本") and "由 AI 提案" in text
    assert set(yaml.safe_load(text)) == {"potential_products", "restock_priority"}
    assert client.get("/decisions/restock_priority").json()["status"] == "ready"
    again = client.post("/assistant/playbooks/save", json={"name": "restock_priority", "decision": proposal["decision"]})
    assert again.status_code == 409


def test_unavailable_without_credentials(spec_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    client = TestClient(create_app(spec_dir, tmp_path / "data"))
    assert client.get("/assistant/status").json()["available"] is False
    assert client.post("/assistant/playbooks", json={"request": "優先補貨的商品"}).status_code == 503


# ---------- OpenRouter ----------


def test_openrouter_requests_skip_anthropic_only_parameters(spec_dir: Path, tmp_path: Path) -> None:
    fake = FakeClaude(_proposal())
    body = _client(spec_dir, tmp_path, fake, "openrouter").post("/assistant/playbooks", json={"request": "優先補貨"}).json()
    assert body["status"] == "ready"
    call = fake.calls[0]
    assert call["model"] == "anthropic/claude-opus-5.5"
    assert "fallbacks" not in call and "betas" not in call
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}


def _auth_headers(client: Any) -> dict[str, str]:
    import anthropic

    options = anthropic._models.FinalRequestOptions.construct(method="post", url="/v1/messages", json_data={})
    request = client._build_request(options)
    return {k.lower(): v for k, v in request.headers.items() if k.lower() in ("x-api-key", "authorization")}


def test_openrouter_takes_priority_and_never_sends_the_anthropic_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-never-leave")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-demo")
    monkeypatch.delenv("GRAINLINE_MODEL", raising=False)
    provider = make_provider()
    assert provider is not None and provider.name == "openrouter"
    assert provider.model == "anthropic/claude-opus-5.5"
    assert str(provider.client.base_url).rstrip("/") == OPENROUTER_BASE_URL
    headers = _auth_headers(provider.client)
    assert headers == {"authorization": "Bearer sk-or-demo"}
    assert "sk-ant-should-never-leave" not in str(headers)


def test_anthropic_is_used_without_openrouter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("GRAINLINE_MODEL", "claude-sonnet-5-5")
    provider = make_provider()
    assert provider is not None and (provider.name, provider.model) == ("anthropic", "claude-sonnet-5-5")


def test_status_reports_the_provider(spec_dir: Path, tmp_path: Path) -> None:
    status = _client(spec_dir, tmp_path, FakeClaude(), "openrouter").get("/assistant/status").json()
    assert status == {"available": True, "provider": "openrouter", "model": "anthropic/claude-opus-5.5", "reason": None}
