"""OpenAIProvider 单元测试（含 agnes 通过同类的端到端路径）。

覆盖：
  - 构造：默认 spec=openai；显式 spec=agnes 用 agnes 默认值；空 api_key 抛错
  - from_env("agnes") 读 AGNES_* env；缺 key 抛错
  - from_env("openai") 读 OPENAI_* env
  - from_env 拒绝非 openai 协议 spec
  - call() 走 /v1/chat/completions + Bearer + 标准 OpenAI 响应解析
  - HTTP 429 / 5xx → RetryableError
  - HTTP 4xx（除 429）→ ValueError
  - 网络异常 → RetryableError
  - 响应 JSON 残缺 → ValueError
  - setup_provider_from_env 优先级：AGNES > MiniMax > OPENAI > Mock
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from pipeline.creators.llm import (
    CompletionResult,
    MockProvider,
    OpenAIProvider,
    PROVIDER_SPECS,
    RetryableError,
    set_provider,
    setup_provider_from_env,
)


# ── 构造 ────────────────────────────────────────────────────

def test_constructor_default_spec_is_openai() -> None:
    """不传 spec → 默认走 'openai'（向后兼容）。"""
    p = OpenAIProvider(api_key="x")
    assert p._spec.name == "openai"  # noqa: SLF001
    assert p._base_url == PROVIDER_SPECS["openai"].default_base_url.rstrip("/")  # noqa: SLF001
    assert p._model == PROVIDER_SPECS["openai"].default_model  # noqa: SLF001


def test_constructor_uses_spec_defaults_for_agnes() -> None:
    """显式 spec=agnes → 用 apihub.agnes-ai.com / agnes-2.0-flash。"""
    spec = PROVIDER_SPECS["agnes"]
    p = OpenAIProvider(api_key="x", spec=spec)
    assert p._spec.name == "agnes"  # noqa: SLF001
    assert p._base_url == "https://apihub.agnes-ai.com/v1"  # noqa: SLF001
    assert p._model == "agnes-2.0-flash"  # noqa: SLF001
    assert p._timeout_s == 60.0  # noqa: SLF001


def test_constructor_explicit_args_override_spec() -> None:
    """显式 base_url/model/timeout_s 覆盖 spec。"""
    p = OpenAIProvider(
        api_key="x",
        spec=PROVIDER_SPECS["agnes"],
        base_url="https://custom.example/v1/",
        model="custom-model",
        timeout_s=120.0,
    )
    assert p._base_url == "https://custom.example/v1"  # rstrip("/")
    assert p._model == "custom-model"  # noqa: SLF001
    assert p._timeout_s == 120.0  # noqa: SLF001


def test_constructor_requires_api_key() -> None:
    """空 api_key → ValueError。"""
    with pytest.raises(ValueError, match="api_key is required"):
        OpenAIProvider(api_key="")


def test_constructor_rejects_non_openai_protocol_spec() -> None:
    """传 spec.protocol != 'openai' → ValueError（防误用 MiniMaxProvider spec）。"""
    with pytest.raises(ValueError, match="requires protocol='openai'"):
        OpenAIProvider(api_key="x", spec=PROVIDER_SPECS["MiniMax"])


# ── from_env ────────────────────────────────────────────────

def test_from_env_agnes_reads_agnes_env(monkeypatch) -> None:
    """from_env('agnes') 读 AGNES_API_KEY / AGNES_BASE_URL / AGNES_MODEL。"""
    monkeypatch.setenv("AGNES_API_KEY", "agnes-key")
    monkeypatch.setenv("AGNES_BASE_URL", "https://apihub.agnes-ai.com/v1")
    monkeypatch.setenv("AGNES_MODEL", "agnes-2.0-flash")
    monkeypatch.setenv("AGNES_TIMEOUT_S", "90")

    p = OpenAIProvider.from_env("agnes")
    assert p._api_key == "agnes-key"  # noqa: SLF001
    assert p._base_url == "https://apihub.agnes-ai.com/v1"  # noqa: SLF001
    assert p._model == "agnes-2.0-flash"  # noqa: SLF001
    assert p._timeout_s == 90.0  # noqa: SLF001


def test_from_env_agnes_uses_spec_defaults_when_overrides_unset(monkeypatch) -> None:
    """AGNES_BASE_URL/MODEL 未设 → 走 spec 默认值。"""
    monkeypatch.setenv("AGNES_API_KEY", "k")
    monkeypatch.delenv("AGNES_BASE_URL", raising=False)
    monkeypatch.delenv("AGNES_MODEL", raising=False)
    monkeypatch.delenv("AGNES_TIMEOUT_S", raising=False)

    p = OpenAIProvider.from_env("agnes")
    assert p._base_url == "https://apihub.agnes-ai.com/v1"  # noqa: SLF001
    assert p._model == "agnes-2.0-flash"  # noqa: SLF001


def test_from_env_missing_agnes_key_raises(monkeypatch) -> None:
    """AGNES_API_KEY 未设 → ValueError（不静默回退）。"""
    monkeypatch.delenv("AGNES_API_KEY", raising=False)
    with pytest.raises(ValueError, match="AGNES_API_KEY"):
        OpenAIProvider.from_env("agnes")


def test_from_env_openai_reads_openai_env(monkeypatch) -> None:
    """from_env('openai') 读 OPENAI_* env。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    p = OpenAIProvider.from_env("openai")
    assert p._api_key == "sk-openai"  # noqa: SLF001
    assert p._base_url == "https://api.openai.com/v1"  # noqa: SLF001
    assert p._model == "gpt-4o-mini"  # noqa: SLF001


def test_from_env_unknown_provider_raises_keyerror() -> None:
    with pytest.raises(KeyError, match="unknown provider"):
        OpenAIProvider.from_env("nonexistent")


def test_from_env_rejects_non_openai_protocol(monkeypatch) -> None:
    """from_env('MiniMax') → ValueError（protocol 不匹配）。"""
    monkeypatch.setenv("MINIMAX_API_KEY", "k")
    with pytest.raises(ValueError, match="not 'openai'"):
        OpenAIProvider.from_env("MiniMax")


# ── call() 走标准 OpenAI 协议 ──────────────────────────────

def _mock_httpx_response(status_code: int, json_body: dict | str) -> MagicMock:
    """构造 mock httpx 响应（status_code + json() + text）。"""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = json.dumps(json_body) if isinstance(json_body, dict) else str(json_body)
    resp.json.side_effect = (
        (lambda: json.loads(resp.text)) if isinstance(json_body, dict) else None
    )
    return resp


def test_call_sends_correct_request_to_chat_completions() -> None:
    """POST {base_url}/chat/completions + Authorization: Bearer + 标准 body。"""
    spec = PROVIDER_SPECS["agnes"]
    p = OpenAIProvider(api_key="sk-test", spec=spec)
    fake_resp = _mock_httpx_response(200, {
        "choices": [{"message": {"content": "hello"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    })
    with patch("httpx.post", return_value=fake_resp) as mock_post:
        result = p.call("ping", "agnes-2.0-flash", max_tokens=20)

    # URL 校验
    args, kwargs = mock_post.call_args
    assert args[0] == "https://apihub.agnes-ai.com/v1/chat/completions"
    # Headers 校验
    assert kwargs["headers"]["Authorization"] == "Bearer sk-test"
    assert kwargs["headers"]["Content-Type"] == "application/json"
    # Body 校验
    body = kwargs["json"]
    assert body["model"] == "agnes-2.0-flash"
    assert body["max_tokens"] == 20
    assert body["messages"] == [{"role": "user", "content": "ping"}]
    # 返回
    assert isinstance(result, CompletionResult)
    assert result.text == "hello"
    assert result.input_tokens == 10
    assert result.output_tokens == 5


def test_call_429_raises_retryable() -> None:
    """HTTP 429 → RetryableError（wrapper 会重试）。"""
    p = OpenAIProvider(api_key="x", spec=PROVIDER_SPECS["agnes"])
    fake_resp = _mock_httpx_response(429, {"error": "rate limit"})
    with patch("httpx.post", return_value=fake_resp):
        with pytest.raises(RetryableError, match="HTTP 429"):
            p.call("p", "m", 10)


def test_call_500_raises_retryable() -> None:
    """HTTP 5xx → RetryableError。"""
    p = OpenAIProvider(api_key="x", spec=PROVIDER_SPECS["agnes"])
    fake_resp = _mock_httpx_response(503, {"error": "unavailable"})
    with patch("httpx.post", return_value=fake_resp):
        with pytest.raises(RetryableError, match="HTTP 503"):
            p.call("p", "m", 10)


def test_call_400_raises_valueerror() -> None:
    """HTTP 4xx（除 429）→ ValueError（契约错误，不重试）。"""
    p = OpenAIProvider(api_key="x", spec=PROVIDER_SPECS["agnes"])
    fake_resp = _mock_httpx_response(400, {"error": "bad request"})
    with patch("httpx.post", return_value=fake_resp):
        with pytest.raises(ValueError, match="HTTP 400"):
            p.call("p", "m", 10)


def test_call_network_error_raises_retryable() -> None:
    """网络异常（RequestError）→ RetryableError。"""
    import httpx
    p = OpenAIProvider(api_key="x", spec=PROVIDER_SPECS["agnes"])
    with patch("httpx.post", side_effect=httpx.RequestError("conn refused")):
        with pytest.raises(RetryableError, match="network error"):
            p.call("p", "m", 10)


def test_call_malformed_json_raises_valueerror() -> None:
    """响应非 JSON → ValueError。"""
    p = OpenAIProvider(api_key="x", spec=PROVIDER_SPECS["agnes"])
    resp = MagicMock()
    resp.status_code = 200
    resp.text = "<html>oops</html>"
    resp.json.side_effect = json.JSONDecodeError("err", "doc", 0)
    with patch("httpx.post", return_value=resp):
        with pytest.raises(ValueError, match="response not JSON"):
            p.call("p", "m", 10)


def test_call_missing_choices_raises_valueerror() -> None:
    """响应结构残缺（无 choices）→ ValueError。"""
    p = OpenAIProvider(api_key="x", spec=PROVIDER_SPECS["agnes"])
    resp = _mock_httpx_response(200, {"usage": {}})
    with patch("httpx.post", return_value=resp):
        with pytest.raises(ValueError, match="malformed"):
            p.call("p", "m", 10)


def test_call_missing_usage_defaults_to_zero_tokens() -> None:
    """usage 字段缺失 → token 计 0（不让协议差异阻塞）。"""
    p = OpenAIProvider(api_key="x", spec=PROVIDER_SPECS["agnes"])
    resp = _mock_httpx_response(200, {
        "choices": [{"message": {"content": "hi"}}],
    })
    with patch("httpx.post", return_value=resp):
        result = p.call("p", "m", 10)
    assert result.text == "hi"
    assert result.input_tokens == 0
    assert result.output_tokens == 0


# ── setup_provider_from_env 优先级 ─────────────────────────

def test_setup_provider_agnes_wins_over_minimax(monkeypatch) -> None:
    """AGNES_API_KEY 存在 → OpenAIProvider(agnes)；MINIMAX 忽略。"""
    monkeypatch.setenv("AGNES_API_KEY", "ak")
    monkeypatch.setenv("MINIMAX_API_KEY", "mk")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ank")
    monkeypatch.setenv("OPENAI_API_KEY", "ok")

    p = setup_provider_from_env()
    assert isinstance(p, OpenAIProvider)
    assert p._spec.name == "agnes"  # noqa: SLF001
    assert p._api_key == "ak"  # noqa: SLF001


def test_setup_provider_falls_back_to_minimax(monkeypatch) -> None:
    """AGNES 未设，MINIMAX 有 → MiniMaxProvider。"""
    monkeypatch.delenv("AGNES_API_KEY", raising=False)
    monkeypatch.setenv("MINIMAX_API_KEY", "mk")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from pipeline.creators.llm import MiniMaxProvider
    p = setup_provider_from_env()
    assert isinstance(p, MiniMaxProvider)


def test_setup_provider_falls_back_to_openai(monkeypatch) -> None:
    """AGNES / MINIMAX 都没设，OPENAI 有 → OpenAIProvider(openai)。"""
    monkeypatch.delenv("AGNES_API_KEY", raising=False)
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "ok")
    p = setup_provider_from_env()
    assert isinstance(p, OpenAIProvider)
    assert p._spec.name == "openai"  # noqa: SLF001


def test_setup_provider_falls_back_to_mock(monkeypatch) -> None:
    """三个 key 全没设 → MockProvider。"""
    monkeypatch.delenv("AGNES_API_KEY", raising=False)
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    p = setup_provider_from_env()
    assert isinstance(p, MockProvider)
