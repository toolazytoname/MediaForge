"""MiniMax Provider 单元测试（M2-2 真实冒烟）。

覆盖：
  - from_env 在 MINIMAX_API_KEY 与 ANTHROPIC_API_KEY 都缺时抛 ValueError
  - from_env 回退到 ANTHROPIC_* env 变量
  - 调 Anthropic 兼容 /v1/messages，构造正确 headers/body
  - HTTP 200 → 解析 content[] 与 usage，输出 CompletionResult
  - HTTP 429 / 5xx / 529 → RetryableError（wrapper 重试）
  - HTTP 4xx（除 429）→ ValueError（契约错误）
  - 网络异常（RequestError）→ RetryableError
  - 响应 JSON 残缺 → ValueError
  - setup_provider_from_env 在有 ANTHROPIC_API_KEY 时返回 MiniMaxProvider
  - 缺 key 时回退到 MockProvider
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from pipeline.creators.llm import (
    CompletionResult,
    MockProvider,
    MiniMaxProvider,
    RetryableError,
    set_provider,
    setup_provider_from_env,
)


# ── from_env ────────────────────────────────────────────────

def test_from_env_missing_key_raises(monkeypatch) -> None:
    """两个 env var 都没设 → ValueError。"""
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError, match="env var not set"):
        MiniMaxProvider.from_env()


def test_from_env_prefers_minimax_over_anthropic(monkeypatch) -> None:
    """MINIMAX_API_KEY 优先于 ANTHROPIC_API_KEY。"""
    monkeypatch.setenv("MINIMAX_API_KEY", "minimax-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
    monkeypatch.setenv("MINIMAX_BASE_URL", "https://custom.example/v1")

    p = MiniMaxProvider.from_env()
    assert p._api_key == "minimax-key"  # noqa: SLF001
    assert p._base_url == "https://custom.example/v1"  # noqa: SLF001


def test_from_env_falls_back_to_anthropic_vars(monkeypatch) -> None:
    """MINIMAX_* 未设时回退到 ANTHROPIC_*。"""
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.minimaxi.com/anthropic")
    monkeypatch.setenv("ANTHROPIC_MODEL", "MiniMax-M3")

    p = MiniMaxProvider.from_env()
    assert p._api_key == "anthropic-key"  # noqa: SLF001
    assert p._base_url == "https://api.minimaxi.com/anthropic"  # noqa: SLF001
    assert p._model == "MiniMax-M3"  # noqa: SLF001


def test_constructor_requires_api_key() -> None:
    """空 api_key → ValueError。"""
    with pytest.raises(ValueError, match="api_key is required"):
        MiniMaxProvider(api_key="")


# ── call（mock httpx）─────────────────────────────────────

def _mock_httpx_response(
    status_code: int = 200,
    json_data: dict | None = None,
    text: str = "",
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text or json.dumps(json_data or {})
    resp.json.return_value = json_data or {}
    return resp


def test_call_success_parses_anthropic_response() -> None:
    """HTTP 200 + 标准 Anthropic response → CompletionResult。"""
    response_data = {
        "content": [{"type": "text", "text": "你好世界"}],
        "usage": {"input_tokens": 100, "output_tokens": 50},
    }
    mock_resp = _mock_httpx_response(200, response_data)

    with patch("httpx.post", return_value=mock_resp) as mock_post:
        p = MiniMaxProvider(api_key="test-key", base_url="https://example/v1")
        result = p.call(prompt="hi", model="MiniMax-M3", max_tokens=1024)

    assert isinstance(result, CompletionResult)
    assert result.text == "你好世界"
    assert result.input_tokens == 100
    assert result.output_tokens == 50

    # 验证请求格式（Anthropic /v1/messages + x-api-key + anthropic-version）
    args, kwargs = mock_post.call_args
    call_kwargs = kwargs
    call_args = args
    assert call_kwargs["headers"]["x-api-key"] == "test-key"
    assert call_kwargs["headers"]["anthropic-version"] == "2023-06-01"
    body = call_kwargs["json"]
    assert body["model"] == "MiniMax-M3"
    assert body["max_tokens"] == 1024
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    # URL 第一个位置参数
    assert call_args[0] == "https://example/v1/v1/messages"


def test_call_concatenates_multiple_text_blocks() -> None:
    """Anthropic 响应可能含多 text 块 → 拼成一段。"""
    response_data = {
        "content": [
            {"type": "text", "text": "Part 1. "},
            {"type": "text", "text": "Part 2."},
        ],
        "usage": {"input_tokens": 50, "output_tokens": 30},
    }
    with patch("httpx.post", return_value=_mock_httpx_response(200, response_data)):
        p = MiniMaxProvider(api_key="test-key")
        result = p.call("hi", "MiniMax-M3", 1024)
    assert result.text == "Part 1. Part 2."


@pytest.mark.parametrize("status_code", [429, 500, 502, 503, 529])
def test_call_retryable_status_codes(status_code: int) -> None:
    """429/5xx/529 → RetryableError（wrapper 会重试）。"""
    with patch(
        "httpx.post", return_value=_mock_httpx_response(status_code, text="err")
    ):
        p = MiniMaxProvider(api_key="test-key")
        with pytest.raises(RetryableError):
            p.call("hi", "MiniMax-M3", 1024)


@pytest.mark.parametrize("status_code", [400, 401, 403, 404])
def test_call_non_retryable_4xx(status_code: int) -> None:
    """非 429 的 4xx → ValueError（契约错误，不重试）。"""
    with patch(
        "httpx.post", return_value=_mock_httpx_response(status_code, text="bad")
    ):
        p = MiniMaxProvider(api_key="test-key")
        with pytest.raises(ValueError, match="HTTP"):
            p.call("hi", "MiniMax-M3", 1024)


def test_call_network_error_is_retryable() -> None:
    """网络异常（DNS / 超时）→ RetryableError。"""
    import httpx

    with patch("httpx.post", side_effect=httpx.RequestError("dns fail")):
        p = MiniMaxProvider(api_key="test-key")
        with pytest.raises(RetryableError, match="network error"):
            p.call("hi", "MiniMax-M3", 1024)


def test_call_malformed_json_raises_value_error() -> None:
    """响应非 JSON → ValueError。"""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.side_effect = json.JSONDecodeError("err", "", 0)
    mock_resp.text = "not json"
    with patch("httpx.post", return_value=mock_resp):
        p = MiniMaxProvider(api_key="test-key")
        with pytest.raises(ValueError, match="not JSON"):
            p.call("hi", "MiniMax-M3", 1024)


def test_call_missing_content_blocks_raises() -> None:
    """响应缺 content 字段 → ValueError。"""
    with patch(
        "httpx.post",
        return_value=_mock_httpx_response(200, {"usage": {}}),
    ):
        p = MiniMaxProvider(api_key="test-key")
        with pytest.raises(ValueError, match="malformed"):
            p.call("hi", "MiniMax-M3", 1024)


# ── setup_provider_from_env ─────────────────────────────────

def test_setup_provider_uses_minimax_when_key_set(monkeypatch) -> None:
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    set_provider(MockProvider())  # 重置
    p = setup_provider_from_env()
    assert isinstance(p, MiniMaxProvider)


def test_setup_provider_falls_back_to_anthropic_key(monkeypatch) -> None:
    """MINIMAX_API_KEY 未设但 ANTHROPIC_API_KEY 设了 → 仍走 MiniMax。"""
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    set_provider(MockProvider())
    p = setup_provider_from_env()
    assert isinstance(p, MiniMaxProvider)


def test_setup_provider_falls_back_to_mock_when_no_key(monkeypatch) -> None:
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    set_provider(MockProvider())
    p = setup_provider_from_env()
    assert isinstance(p, MockProvider)