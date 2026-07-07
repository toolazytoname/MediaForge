"""PROVIDER_SPECS 注册表 + build_provider 工厂 测试（M1-9）。

覆盖：
  - PROVIDER_SPECS 有 4 个条目：mock / MiniMax / anthropic / openai
  - 各 spec 关键字段（protocol / supports_response_format / temperature 上下限 /
    extra_fence_strip / env_var_prefix）
  - MiniMaxProvider 默认值 = spec.default_*
  - 显式参数覆盖 spec 默认值
  - MiniMaxProvider.from_env() 行为完全等价（MINIMAX_* 优先，回退 ANTHROPIC_*）
  - build_provider("MiniMax", api_key="x") ≡ MiniMaxProvider(api_key="x")
  - build_provider("anthropic", ...) → NotImplementedError
"""
from __future__ import annotations

import pytest

from pipeline.creators.llm import (
    PROVIDER_SPECS,
    ProviderSpec,
    MiniMaxProvider,
    build_provider,
)


# ── ProviderSpec 注册表结构 ─────────────────────────────────

def test_provider_specs_has_four_entries() -> None:
    assert set(PROVIDER_SPECS.keys()) == {"mock", "MiniMax", "anthropic", "openai"}


def test_each_value_is_provider_spec() -> None:
    for name, spec in PROVIDER_SPECS.items():
        assert isinstance(spec, ProviderSpec), f"{name} not a ProviderSpec"
        assert spec.name == name


def test_minimax_spec_fields() -> None:
    """MiniMax 走 Anthropic 兼容协议，temperature 0~1，不原生支持 response_format。"""
    spec = PROVIDER_SPECS["MiniMax"]
    assert spec.protocol == "anthropic"
    assert spec.supports_response_format is False
    assert spec.min_temperature == 0.0
    assert spec.max_temperature == 1.0
    assert spec.extra_fence_strip is False
    assert spec.env_var_prefix == "MINIMAX"
    # 默认值正确
    assert spec.default_base_url == "https://api.minimaxi.com/anthropic"
    assert spec.default_model == "MiniMax-M3"
    assert spec.default_timeout_s == 60.0
    assert spec.default_api_version == "2023-06-01"


def test_anthropic_spec_fields() -> None:
    spec = PROVIDER_SPECS["anthropic"]
    assert spec.protocol == "anthropic"
    assert spec.supports_response_format is False
    assert spec.min_temperature == 0.0
    assert spec.max_temperature == 1.0
    assert spec.env_var_prefix == "ANTHROPIC"


def test_openai_spec_fields() -> None:
    """OpenAI 协议原生支持 response_format，temperature 上限 2.0。"""
    spec = PROVIDER_SPECS["openai"]
    assert spec.protocol == "openai"
    assert spec.supports_response_format is True
    assert spec.min_temperature == 0.0
    assert spec.max_temperature == 2.0
    assert spec.env_var_prefix == "OPENAI"


def test_mock_spec_has_no_temperature_limits() -> None:
    """Mock 不限 temperature（用于测试边界）。"""
    spec = PROVIDER_SPECS["mock"]
    assert spec.protocol == "mock"
    assert spec.min_temperature is None
    assert spec.max_temperature is None
    assert spec.supports_response_format is False
    assert spec.extra_fence_strip is False


def test_all_specs_extra_fence_strip_default_false() -> None:
    """当前所有 spec extra_fence_strip=False（围栏剥离由 callers 处理）。"""
    for name, spec in PROVIDER_SPECS.items():
        assert spec.extra_fence_strip is False, (
            f"{name} unexpectedly has extra_fence_strip=True"
        )


# ── MiniMaxProvider 初始化读 spec ──────────────────────────

def test_minimax_uses_spec_defaults_when_no_overrides(monkeypatch) -> None:
    """MiniMaxProvider(api_key="x") 默认值 = spec.default_*。"""
    # 清环境，确保默认值来自 spec 而非 env
    for v in (
        "MINIMAX_API_KEY", "ANTHROPIC_API_KEY",
        "MINIMAX_BASE_URL", "ANTHROPIC_BASE_URL",
        "MINIMAX_MODEL", "ANTHROPIC_MODEL",
        "MINIMAX_TIMEOUT_S", "MINIMAX_API_VERSION",
    ):
        monkeypatch.delenv(v, raising=False)

    spec = PROVIDER_SPECS["MiniMax"]
    p = MiniMaxProvider(api_key="x")
    assert p._api_key == "x"  # noqa: SLF001
    assert p._base_url == spec.default_base_url.rstrip("/")  # noqa: SLF001
    assert p._model == spec.default_model  # noqa: SLF001
    assert p._timeout_s == spec.default_timeout_s  # noqa: SLF001
    assert p._api_version == spec.default_api_version  # noqa: SLF001


def test_minimax_explicit_args_override_spec_defaults() -> None:
    """显式 base_url/model/timeout_s/api_version 覆盖 spec。"""
    p = MiniMaxProvider(
        api_key="x",
        base_url="https://custom.example/v1/",
        model="custom-model",
        timeout_s=120.0,
        api_version="2024-01-01",
    )
    # rstrip("/") 必须保留
    assert p._base_url == "https://custom.example/v1"  # noqa: SLF001
    assert p._model == "custom-model"  # noqa: SLF001
    assert p._timeout_s == 120.0  # noqa: SLF001
    assert p._api_version == "2024-01-01"  # noqa: SLF001


def test_minimax_constructor_still_requires_api_key() -> None:
    """空 api_key → ValueError（契约保留）。"""
    with pytest.raises(ValueError, match="api_key is required"):
        MiniMaxProvider(api_key="")


# ── MiniMaxProvider.from_env() 行为不变 ────────────────────

def test_from_env_minimax_prefers_anthropic_fallback(monkeypatch) -> None:
    """MINIMAX_API_KEY 优先；ANTHROPIC_* env 仍作为回退（用户实测兼容）。"""
    monkeypatch.setenv("MINIMAX_API_KEY", "mk")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ak")
    monkeypatch.setenv("MINIMAX_BASE_URL", "https://custom.example/v1")
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)

    p = MiniMaxProvider.from_env()
    assert p._api_key == "mk"  # noqa: SLF001
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


def test_from_env_missing_any_key_raises(monkeypatch) -> None:
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError, match="env var not set"):
        MiniMaxProvider.from_env()


# ── build_provider 工厂 ────────────────────────────────────

def test_build_provider_minimax_equivalent_to_constructor() -> None:
    """build_provider("MiniMax", api_key="x") ≡ MiniMaxProvider(api_key="x")。"""
    p1 = build_provider("MiniMax", api_key="x")
    p2 = MiniMaxProvider(api_key="x")
    assert isinstance(p1, MiniMaxProvider)
    assert p1._api_key == p2._api_key  # noqa: SLF001
    assert p1._base_url == p2._base_url  # noqa: SLF001
    assert p1._model == p2._model  # noqa: SLF001
    assert p1._timeout_s == p2._timeout_s  # noqa: SLF001
    assert p1._api_version == p2._api_version  # noqa: SLF001


def test_build_provider_minimax_passes_overrides() -> None:
    p = build_provider(
        "MiniMax", api_key="x", base_url="https://other/v1", model="m1"
    )
    assert p._base_url == "https://other/v1"  # noqa: SLF001
    assert p._model == "m1"  # noqa: SLF001


def test_build_provider_anthropic_not_implemented() -> None:
    """anthropic 仅在 PROVIDER_SPECS 占位，类未实装 → NotImplementedError。"""
    with pytest.raises(NotImplementedError):
        build_provider("anthropic", api_key="x")


def test_build_provider_openai_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        build_provider("openai", api_key="x")


def test_build_provider_unknown_raises_keyerror() -> None:
    """未知 provider 名 → KeyError。"""
    with pytest.raises(KeyError):
        build_provider("nonexistent", api_key="x")
