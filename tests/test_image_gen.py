"""image_gen 模块测试。

覆盖：
  - MiniMaxImageProvider 构造 / from_env / 参数校验
  - HTTP 调用的请求构造（URL、headers、body）
  - HTTP 响应解析（成功 / 429 / 5xx / 4xx / 网络错误 / 响应残缺）
  - generate_image 顶层入口：重试、原子写、审计、预算
  - 单条失败不阻断（高层测试在 CLI 测试中覆盖）

TDD 风格：先 mock urllib.request.urlopen 验证调用契约，再测 wrapper。
"""
from __future__ import annotations

import base64
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib import error as _urlerr, request

import pytest

from pipeline.creators import image_gen
from pipeline.creators.image_gen import (
    GeneratedImage,
    ImageProvider,
    MiniMaxImageProvider,
    RetryableError,
    generate_image,
    setup_provider_from_env,
)
from pipeline.utils.errors import BudgetExceeded


# ── 固定 fixture：1x1 透明 PNG（base64 编码）─────────────
_VALID_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9aw"
    "AAAABJRU5ErkJggg=="
)
_VALID_PNG_BYTES = base64.b64decode(_VALID_PNG_B64)


# ── helper：构造 mock urlopen 响应 ──────────────────────

def _mock_urlopen_response(status: int = 200, json_body: dict | None = None) -> MagicMock:
    """构造 mock context-manager，模拟 urlopen 返回。"""
    mock_resp = MagicMock()
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.read = MagicMock(return_value=json.dumps(json_body).encode("utf-8"))
    # status 在 urlopen 成功时没意义；放在 __enter__ 内部
    mock_resp.status = status
    return mock_resp


def _mock_http_error(status: int, body: str = "error") -> _urlerr.HTTPError:
    """构造 mock HTTPError。"""
    return _urlerr.HTTPError(
        url="https://api.minimaxi.com/v1/image_generation",
        code=status,
        msg="err",
        hdrs={},  # type: ignore[arg-type]
        fp=io.BytesIO(body.encode("utf-8")),
    )


# ── MiniMaxImageProvider：构造 & from_env ───────────────

class TestMiniMaxImageProviderInit:
    def test_requires_api_key(self):
        with pytest.raises(ValueError, match="api_key is required"):
            MiniMaxImageProvider(api_key="")

    def test_default_values(self):
        p = MiniMaxImageProvider(api_key="k")
        assert p._base_url == "https://api.minimaxi.com/v1"
        assert p._model == "image-01"
        assert p._timeout_s == 90.0

    def test_explicit_overrides(self):
        p = MiniMaxImageProvider(
            api_key="k",
            base_url="https://custom.example/v1",
            model="image-99",
            timeout_s=120.0,
        )
        assert p._base_url == "https://custom.example/v1"
        assert p._model == "image-99"
        assert p._timeout_s == 120.0

    def test_from_env_reads_minimax_image_api_key(self, monkeypatch):
        monkeypatch.setenv("MINIMAX_IMAGE_API_KEY", "img-key-1")
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        p = MiniMaxImageProvider.from_env()
        assert p._api_key == "img-key-1"

    def test_from_env_falls_back_to_minimax_api_key(self, monkeypatch):
        monkeypatch.delenv("MINIMAX_IMAGE_API_KEY", raising=False)
        monkeypatch.setenv("MINIMAX_API_KEY", "shared-key")
        p = MiniMaxImageProvider.from_env()
        assert p._api_key == "shared-key"

    def test_from_env_raises_when_no_key(self, monkeypatch):
        monkeypatch.delenv("MINIMAX_IMAGE_API_KEY", raising=False)
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        with pytest.raises(ValueError, match="MINIMAX_IMAGE_API_KEY"):
            MiniMaxImageProvider.from_env()

    def test_from_env_overrides(self, monkeypatch):
        monkeypatch.setenv("MINIMAX_IMAGE_API_KEY", "k")
        monkeypatch.setenv("MINIMAX_IMAGE_BASE_URL", "https://x/v1")
        monkeypatch.setenv("MINIMAX_IMAGE_MODEL", "image-99")
        monkeypatch.setenv("MINIMAX_IMAGE_TIMEOUT_S", "120")
        p = MiniMaxImageProvider.from_env()
        assert p._base_url == "https://x/v1"
        assert p._model == "image-99"
        assert p._timeout_s == 120.0


# ── MiniMaxImageProvider.call：参数校验 ──────────────────

class TestMiniMaxImageProviderCallValidation:
    def _make(self):
        return MiniMaxImageProvider(api_key="k")

    def test_invalid_aspect_ratio_raises_value_error(self):
        p = self._make()
        with pytest.raises(ValueError, match="invalid aspect_ratio"):
            p.call("prompt", aspect_ratio="99:1", n=1, response_format="base64")

    @pytest.mark.parametrize("ratio", ["1:1", "16:9", "9:16", "4:3", "3:4"])
    def test_valid_aspect_ratios(self, ratio):
        """合法 aspect_ratio 不在参数校验阶段抛错（会被 mock urlopen 接管）。"""
        p = self._make()
        with patch("pipeline.creators.image_gen.request.urlopen") as mock:
            mock.return_value = _mock_urlopen_response(
                json_body={"data": {"image_base64": [_VALID_PNG_B64]}}
            )
            result = p.call("p", aspect_ratio=ratio, n=1, response_format="base64")
            assert len(result) == 1

    def test_n_out_of_range_raises(self):
        p = self._make()
        with pytest.raises(ValueError, match="n must be 1..4"):
            p.call("p", aspect_ratio="1:1", n=0, response_format="base64")
        with pytest.raises(ValueError, match="n must be 1..4"):
            p.call("p", aspect_ratio="1:1", n=5, response_format="base64")


# ── MiniMaxImageProvider.call：HTTP 调用契约 ─────────────

class TestMiniMaxImageProviderCallHttp:
    def _make(self):
        return MiniMaxImageProvider(api_key="secret-key-123")

    def _capture_request(self, mock_urlopen) -> dict:
        """提取 mock urlopen 收到的 Request，验证 URL/headers/body。

        urllib 的 Request.headers 是 email.message.Message，case 保留为
        添加时的形态（"Content-Type" → 读出 "Content-type"）。
        这里统一小写化便于断言。
        """
        assert mock_urlopen.call_count == 1
        req = mock_urlopen.call_args[0][0]
        return {
            "url": req.full_url,
            "headers": {k.lower(): v for k, v in req.headers.items()},
            "body": json.loads(req.data.decode("utf-8")),
            "method": req.get_method(),
        }

    def test_success_url_and_headers_and_body(self):
        p = self._make()
        with patch("pipeline.creators.image_gen.request.urlopen") as mock:
            mock.return_value = _mock_urlopen_response(
                json_body={"data": {"image_base64": [_VALID_PNG_B64]}}
            )
            result = p.call(
                "a red circle on white background",
                aspect_ratio="16:9",
                n=1,
                response_format="base64",
            )
        captured = self._capture_request(mock)
        assert captured["url"] == "https://api.minimaxi.com/v1/image_generation"
        assert captured["headers"]["authorization"] == "Bearer secret-key-123"
        assert captured["headers"]["content-type"] == "application/json"
        assert captured["method"] == "POST"
        assert captured["body"] == {
            "model": "image-01",
            "prompt": "a red circle on white background",
            "aspect_ratio": "16:9",
            "n": 1,
            "response_format": "base64",
        }
        assert result == [_VALID_PNG_BYTES]

    def test_429_raises_retryable(self):
        p = self._make()
        with patch("pipeline.creators.image_gen.request.urlopen") as mock:
            mock.side_effect = _mock_http_error(429, "rate limited")
            with pytest.raises(RetryableError, match="HTTP 429"):
                p.call("p", aspect_ratio="1:1", n=1, response_format="base64")

    def test_500_raises_retryable(self):
        p = self._make()
        with patch("pipeline.creators.image_gen.request.urlopen") as mock:
            mock.side_effect = _mock_http_error(500, "internal error")
            with pytest.raises(RetryableError, match="HTTP 500"):
                p.call("p", aspect_ratio="1:1", n=1, response_format="base64")

    @pytest.mark.parametrize("code", [400, 401, 403, 404])
    def test_4xx_raises_value_error(self, code):
        p = self._make()
        with patch("pipeline.creators.image_gen.request.urlopen") as mock:
            mock.side_effect = _mock_http_error(code, f"err {code}")
            with pytest.raises(ValueError, match=f"HTTP {code}"):
                p.call("p", aspect_ratio="1:1", n=1, response_format="base64")

    def test_timeout_raises_retryable(self):
        p = self._make()
        with patch("pipeline.creators.image_gen.request.urlopen") as mock:
            mock.side_effect = TimeoutError("read timeout")
            with pytest.raises(RetryableError, match="network"):
                p.call("p", aspect_ratio="1:1", n=1, response_format="base64")

    def test_connection_error_raises_retryable(self):
        p = self._make()
        with patch("pipeline.creators.image_gen.request.urlopen") as mock:
            mock.side_effect = OSError("Connection refused")
            with pytest.raises(RetryableError, match="network"):
                p.call("p", aspect_ratio="1:1", n=1, response_format="base64")

    # ── 响应解析失败 ──

    def test_response_not_dict_raises(self):
        p = self._make()
        with patch("pipeline.creators.image_gen.request.urlopen") as mock:
            mock.return_value = _mock_urlopen_response(json_body=["not", "a", "dict"])
            with pytest.raises(ValueError, match="response not a dict"):
                p.call("p", aspect_ratio="1:1", n=1, response_format="base64")

    def test_data_missing_raises(self):
        p = self._make()
        with patch("pipeline.creators.image_gen.request.urlopen") as mock:
            mock.return_value = _mock_urlopen_response(json_body={"wrong": "field"})
            with pytest.raises(ValueError, match="response.data not a dict"):
                p.call("p", aspect_ratio="1:1", n=1, response_format="base64")

    def test_data_not_dict_raises(self):
        """data 字段是 list 而不是 dict 时也要拒绝。"""
        p = self._make()
        with patch("pipeline.creators.image_gen.request.urlopen") as mock:
            mock.return_value = _mock_urlopen_response(
                json_body={"data": ["not", "a", "dict"]}
            )
            with pytest.raises(ValueError, match="response.data not a dict"):
                p.call("p", aspect_ratio="1:1", n=1, response_format="base64")

    def test_data_empty_dict_raises_image_base64_missing(self):
        """data 是空 dict（无 image_base64）时报 image_base64 missing。"""
        p = self._make()
        with patch("pipeline.creators.image_gen.request.urlopen") as mock:
            mock.return_value = _mock_urlopen_response(json_body={"data": {}})
            with pytest.raises(ValueError, match="image_base64 missing/empty"):
                p.call("p", aspect_ratio="1:1", n=1, response_format="base64")

    def test_image_base64_empty_list_raises(self):
        p = self._make()
        with patch("pipeline.creators.image_gen.request.urlopen") as mock:
            mock.return_value = _mock_urlopen_response(
                json_body={"data": {"image_base64": []}}
            )
            with pytest.raises(ValueError, match="image_base64 missing/empty"):
                p.call("p", aspect_ratio="1:1", n=1, response_format="base64")

    def test_image_base64_invalid_decode_raises(self):
        p = self._make()
        with patch("pipeline.creators.image_gen.request.urlopen") as mock:
            mock.return_value = _mock_urlopen_response(
                json_body={"data": {"image_base64": ["!!!not-valid-base64!!!"]}}
            )
            with pytest.raises(ValueError, match="decode failed"):
                p.call("p", aspect_ratio="1:1", n=1, response_format="base64")

    def test_multiple_images_returns_all(self):
        p = self._make()
        with patch("pipeline.creators.image_gen.request.urlopen") as mock:
            mock.return_value = _mock_urlopen_response(
                json_body={"data": {"image_base64": [_VALID_PNG_B64, _VALID_PNG_B64]}}
            )
            result = p.call("p", aspect_ratio="1:1", n=2, response_format="base64")
        assert result == [_VALID_PNG_BYTES, _VALID_PNG_BYTES]


# ── generate_image 顶层入口：重试 & 落盘 & 审计 ─────────

class TestGenerateImage:
    """每个测试用 monkeypatch 隔离全局状态（避免污染其他测试文件）。"""

    def _setup(self, monkeypatch):
        image_gen.set_provider(MiniMaxImageProvider(api_key="k"))
        from pipeline.creators import llm as llm_mod
        monkeypatch.setattr(llm_mod, "BUDGET_LIMIT_USD", 80.0)
        # 用 monkeypatch.setitem 改 dict 不会自动回滚，但 monkeypatch 提供
        # .setitem() 配合 del 兜底。这里用 dict.copy 隔离：
        original_prices = dict(llm_mod.MODEL_PRICES)
        llm_mod.MODEL_PRICES["image-01"] = {
            "input": 0.0, "output": 0.0, "per_image_usd": 0.003,
        }
        monkeypatch.setattr(llm_mod, "MODEL_PRICES", {
            **original_prices,
            "image-01": {"input": 0.0, "output": 0.0, "per_image_usd": 0.003},
        })

    def test_atomic_write_creates_png_file(self, tmp_path, monkeypatch):
        self._setup(monkeypatch)
        out = tmp_path / "test.png"
        with patch("pipeline.creators.image_gen.request.urlopen") as mock:
            mock.return_value = _mock_urlopen_response(
                json_body={"data": {"image_base64": [_VALID_PNG_B64]}}
            )
            result = generate_image(
                "a red circle", out_path=out, aspect_ratio="1:1", n=1
            )
        assert out.exists()
        assert out.read_bytes() == _VALID_PNG_BYTES
        assert not (out.with_suffix(out.suffix + ".tmp")).exists()  # tmp 清理
        assert isinstance(result, GeneratedImage)
        assert result.bytes_data == _VALID_PNG_BYTES
        assert result.aspect_ratio == "1:1"
        assert result.model == "image-01"

    def test_no_tmp_residue_on_repeat(self, tmp_path, monkeypatch):
        self._setup(monkeypatch)
        out = tmp_path / "test.png"
        # 写一个残留 .tmp，验证下次写入会清理
        (tmp_path / "test.png.tmp").write_bytes(b"old garbage")
        with patch("pipeline.creators.image_gen.request.urlopen") as mock:
            mock.return_value = _mock_urlopen_response(
                json_body={"data": {"image_base64": [_VALID_PNG_B64]}}
            )
            generate_image("p", out_path=out, aspect_ratio="1:1", n=1)
        assert out.read_bytes() == _VALID_PNG_BYTES
        assert not (out.with_suffix(out.suffix + ".tmp")).exists()

    def test_retry_on_429(self, tmp_path, monkeypatch):
        self._setup(monkeypatch)
        monkeypatch.setattr(image_gen, "_RETRY_BASE_SLEEP_S", 0.0)
        out = tmp_path / "test.png"
        with patch("pipeline.creators.image_gen.request.urlopen") as mock:
            mock.side_effect = [
                _mock_http_error(429, "rate"),
                _mock_urlopen_response(json_body={"data": {"image_base64": [_VALID_PNG_B64]}}),
            ]
            result = generate_image("p", out_path=out, aspect_ratio="1:1", n=1)
        assert mock.call_count == 2
        assert out.read_bytes() == _VALID_PNG_BYTES

    def test_retry_exhausted_raises_retryable(self, tmp_path, monkeypatch):
        self._setup(monkeypatch)
        monkeypatch.setattr(image_gen, "_RETRY_BASE_SLEEP_S", 0.0)
        monkeypatch.setattr(image_gen, "_RETRY_MAX_ATTEMPTS", 3)
        out = tmp_path / "test.png"
        with patch("pipeline.creators.image_gen.request.urlopen") as mock:
            mock.side_effect = _mock_http_error(500, "down")
            with pytest.raises(RetryableError, match="HTTP 500"):
                generate_image("p", out_path=out, aspect_ratio="1:1", n=1)
        assert mock.call_count == 3  # 1 + 2 retries
        assert not out.exists()  # 失败不留残文件

    def test_value_error_not_retried(self, tmp_path, monkeypatch):
        self._setup(monkeypatch)
        monkeypatch.setattr(image_gen, "_RETRY_BASE_SLEEP_S", 0.0)
        out = tmp_path / "test.png"
        with patch("pipeline.creators.image_gen.request.urlopen") as mock:
            mock.side_effect = _mock_http_error(400, "bad prompt")
            with pytest.raises(ValueError, match="HTTP 400"):
                generate_image("p", out_path=out, aspect_ratio="1:1", n=1)
        assert mock.call_count == 1  # 4xx 不重试

    def test_provider_not_initialized_raises(self, tmp_path, monkeypatch):
        self._setup(monkeypatch)
        image_gen.set_provider(None)  # type: ignore[arg-type]
        # _PROVIDER is None now
        with pytest.raises(RuntimeError, match="provider not initialized"):
            generate_image("p", out_path=tmp_path / "x.png", aspect_ratio="1:1", n=1)

    # ── 审计 + 预算 ──

    def test_audit_writes_llm_call_row(self, tmp_path, monkeypatch):
        self._setup(monkeypatch)
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row  # 关键：让 row["col"] 可用
        conn.execute("""
            CREATE TABLE llm_calls (
                id INTEGER PRIMARY KEY,
                stage TEXT, ref_id TEXT, model TEXT,
                input_tokens INTEGER, output_tokens INTEGER,
                cost_usd REAL, created_at TEXT
            )
        """)
        out = tmp_path / "cover.png"
        with patch("pipeline.creators.image_gen.request.urlopen") as mock:
            mock.return_value = _mock_urlopen_response(
                json_body={"data": {"image_base64": [_VALID_PNG_B64]}}
            )
            generate_image(
                "a red circle prompt",
                out_path=out, aspect_ratio="16:9", n=1,
                stage="create_cover", ref_id="c_abc",
                conn=conn,
            )
        row = conn.execute(
            "SELECT stage, ref_id, model, output_tokens, cost_usd "
            "FROM llm_calls WHERE ref_id='c_abc'"
        ).fetchone()
        assert row["stage"] == "create_cover"
        assert row["ref_id"] == "c_abc"
        assert row["model"] == "image-01"
        assert row["output_tokens"] == 1
        # image 按 per_image_usd 计费（绕过 token 算式）
        assert row["cost_usd"] == pytest.approx(0.003)

    def test_budget_exceeded_raises_before_call(self, tmp_path, monkeypatch):
        self._setup(monkeypatch)
        from pipeline.creators import llm as llm_mod
        # 设置 budget = 0 → 任何 image 估算都超
        monkeypatch.setattr(llm_mod, "BUDGET_LIMIT_USD", 0.0)
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row  # 关键：让 row["col"] 可用
        conn.execute("""
            CREATE TABLE llm_calls (
                id INTEGER PRIMARY KEY,
                stage TEXT, ref_id TEXT, model TEXT,
                input_tokens INTEGER, output_tokens INTEGER,
                cost_usd REAL, created_at TEXT
            )
        """)
        out = tmp_path / "cover.png"
        with patch("pipeline.creators.image_gen.request.urlopen") as mock:
            with pytest.raises(BudgetExceeded):
                generate_image(
                    "p", out_path=out, aspect_ratio="1:1", n=1,
                    stage="create_cover", ref_id="c_xyz", conn=conn,
                )
        assert mock.call_count == 0  # 预算检查在调用前
        assert not out.exists()


# ── setup_provider_from_env ─────────────────────────────

class TestSetupProviderFromEnv:
    def setup_method(self):
        # 清掉所有 key 防串扰
        for k in ("MINIMAX_IMAGE_API_KEY", "MINIMAX_API_KEY"):
            os.environ.pop(k, None)

    def test_uses_image_key_when_present(self, monkeypatch):
        monkeypatch.setenv("MINIMAX_IMAGE_API_KEY", "img-key")
        monkeypatch.setenv("MINIMAX_API_KEY", "chat-key")
        p = setup_provider_from_env()
        assert isinstance(p, MiniMaxImageProvider)
        assert p._api_key == "img-key"

    def test_falls_back_to_chat_key(self, monkeypatch):
        monkeypatch.setenv("MINIMAX_API_KEY", "chat-key")
        p = setup_provider_from_env()
        assert isinstance(p, MiniMaxImageProvider)
        assert p._api_key == "chat-key"


# ── 冒烟：模块导入 + ABC 形态 ─────────────────────────

def test_image_provider_is_abstract():
    """ImageProvider 不能直接实例化（强制子类实现）。"""
    with pytest.raises(TypeError):
        ImageProvider()  # type: ignore[abstract]


def test_module_imports_cleanly():
    """冒烟：模块无 import 错误。"""
    import pipeline.creators.image_gen  # noqa: F401
    assert hasattr(image_gen, "generate_image")
    assert hasattr(image_gen, "MiniMaxImageProvider")