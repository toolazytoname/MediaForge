"""图像生成模块（M-x：封面 + 文中插图）。

设计（独立模块，不复用 OpenAIProvider — MiniMax image 接口
endpoint/body/response 都和 chat completions 不一样，复用会变 spaghetti）：

  pipeline/creators/image_gen.py
    ImageProvider（ABC）
    MiniMaxImageProvider（POST /v1/image_generation）
    generate_image() 顶层入口（重试 + 预算 + 审计 + 落盘）

成本审计复用 llm_calls 表，stage 用 'create_cover' / 'create_image' 区分。
价格从 llm.MODEL_PRICES 读（image-01 占位 0/0.003 — 见 llm.py 注释）。

配置（env 注入）：
  - MINIMAX_IMAGE_API_KEY    必填；fallback 到 MINIMAX_API_KEY
  - MINIMAX_IMAGE_BASE_URL   默认 https://api.minimaxi.com/v1
  - MINIMAX_IMAGE_MODEL      默认 image-01
  - MINIMAX_IMAGE_TIMEOUT_S  默认 90s（docs 推荐 60–360s）

异常映射：
  - HTTP 429 / 5xx → RetryableError（wrapper 重试）
  - HTTP 4xx (除 429) → ValueError（契约错误，立即抛）
  - 网络/超时 → RetryableError
  - 响应 data.image_base64 缺失/空 → ValueError
"""
from __future__ import annotations

import base64
import json
import os
import sqlite3
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, request


# ── 错误 / 重试参数（与 llm.py 同构）──────────────────────

class RetryableError(Exception):
    """429 / 5xx / 网络瞬时错误——应触发重试。"""


class ImageProvider(ABC):
    """图像 provider 抽象。"""

    @abstractmethod
    def call(
        self,
        prompt: str,
        *,
        aspect_ratio: str,
        n: int,
        response_format: str,
    ) -> list[bytes]:
        """执行一次图像生成。返回 PNG/JPEG bytes 列表（按 n 数量）。

        Raises:
            RetryableError: 瞬时错误，wrapper 会重试
            ValueError: 契约错误，立即抛
        """


# ── MiniMax image-01 provider ──────────────────────────

class MiniMaxImageProvider(ImageProvider):
    """MiniMax 文生图 provider（POST /v1/image_generation）。

    接口来源：https://platform.minimaxi.com/docs/guides/image-generation
    返回 data.image_base64（list[str]，base64 encoded image bytes）。

    两个支持的 model：
      - image-01：基础版，1280×720（16:9）/ 1024×1024（1:1），~190KB JPEG
      - image-01-live：**增强版**，1456×816（16:9）/ ~2 倍细节，~420KB JPEG
                慢一点（实测 18-30s），但质量明显更好（2026-07-08 实测对比）

    默认 image-01-live（注重质量的发布场景）。
    """

    DEFAULT_BASE_URL = "https://api.minimaxi.com/v1"
    DEFAULT_MODEL = "image-01-live"
    DEFAULT_TIMEOUT_S = 120.0  # image-01-live 比基础版慢 ~30%，加大超时

    # 支持的 aspect_ratio（docs 列出常用 1:1 / 16:9 / 9:16）
    VALID_ASPECT_RATIOS = frozenset({"1:1", "16:9", "9:16", "4:3", "3:4"})

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        model: str | None = None,
        timeout_s: float | None = None,
    ) -> None:
        if not api_key:
            raise ValueError(
                "MiniMaxImageProvider: api_key is required "
                "(set MINIMAX_IMAGE_API_KEY or MINIMAX_API_KEY env var)"
            )
        self._api_key = api_key
        self._base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self._model = model or self.DEFAULT_MODEL
        self._timeout_s = timeout_s if timeout_s is not None else self.DEFAULT_TIMEOUT_S

    @classmethod
    def from_env(cls) -> "MiniMaxImageProvider":
        """从 env 构造；找不到 key 抛 ValueError（不静默回退）。"""
        api_key = (
            os.environ.get("MINIMAX_IMAGE_API_KEY")
            or os.environ.get("MINIMAX_API_KEY")
        )
        if not api_key:
            raise ValueError(
                "MiniMaxImageProvider.from_env: MINIMAX_IMAGE_API_KEY "
                "(or MINIMAX_API_KEY) env var not set"
            )
        return cls(
            api_key=api_key,
            base_url=os.environ.get("MINIMAX_IMAGE_BASE_URL"),
            model=os.environ.get("MINIMAX_IMAGE_MODEL"),
            timeout_s=float(
                os.environ.get(
                    "MINIMAX_IMAGE_TIMEOUT_S", cls.DEFAULT_TIMEOUT_S
                )
            ),
        )

    def call(
        self,
        prompt: str,
        *,
        aspect_ratio: str,
        n: int,
        response_format: str = "base64",
    ) -> list[bytes]:
        if aspect_ratio not in self.VALID_ASPECT_RATIOS:
            raise ValueError(
                f"MiniMaxImageProvider: invalid aspect_ratio={aspect_ratio!r}, "
                f"valid={sorted(self.VALID_ASPECT_RATIOS)}"
            )
        if not (1 <= n <= 4):
            raise ValueError(f"MiniMaxImageProvider: n must be 1..4, got {n}")

        body = json.dumps({
            "model": self._model,
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "n": n,
            "response_format": response_format,
        }).encode("utf-8")
        req = request.Request(
            f"{self._base_url}/image_generation",
            data=body,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self._timeout_s) as resp:
                payload = json.loads(resp.read())
        except error.HTTPError as e:
            status = e.code
            err_body = e.read()[:400].decode("utf-8", "replace") if e.fp else ""
            if status == 429 or 500 <= status < 600:
                raise RetryableError(
                    f"MiniMaxImageProvider HTTP {status}: {err_body[:200]}"
                ) from e
            raise ValueError(
                f"MiniMaxImageProvider HTTP {status}: {err_body[:300]}"
            ) from e
        except (TimeoutError, OSError) as e:
            raise RetryableError(
                f"MiniMaxImageProvider network: {type(e).__name__}: {e}"
            ) from e

        # 解析 data.image_base64
        if not isinstance(payload, dict):
            raise ValueError(f"MiniMaxImageProvider: response not a dict: {type(payload).__name__}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValueError(f"MiniMaxImageProvider: response.data not a dict: {data!r}")
        images_b64 = data.get("image_base64")
        if not isinstance(images_b64, list) or not images_b64:
            raise ValueError(
                f"MiniMaxImageProvider: data.image_base64 missing/empty: "
                f"keys={list(data.keys())}, status_resp={payload.get('status_resp', {})!r}"
            )

        # 解码 base64 → bytes
        out: list[bytes] = []
        for i, item in enumerate(images_b64):
            if not isinstance(item, str):
                raise ValueError(
                    f"MiniMaxImageProvider: image_base64[{i}] not a string: "
                    f"{type(item).__name__}"
                )
            try:
                out.append(base64.b64decode(item, validate=True))
            except (ValueError, TypeError) as e:
                raise ValueError(
                    f"MiniMaxImageProvider: image_base64[{i}] decode failed: {e}"
                ) from e
        return out


# ── 顶层 Provider 管理（单例 + env 自动注入）────────────

_PROVIDER: ImageProvider | None = None


def set_provider(provider: ImageProvider) -> None:
    """注入 provider（测试 / CLI 启动用）。"""
    global _PROVIDER
    _PROVIDER = provider


def setup_provider_from_env() -> ImageProvider:
    """CLI 启动调用：从 env 选 provider（现在只支持 MiniMaxImageProvider）。"""
    provider = MiniMaxImageProvider.from_env()
    set_provider(provider)
    return provider


def _require_provider() -> ImageProvider:
    if _PROVIDER is None:
        raise RuntimeError(
            "image_gen: provider not initialized; "
            "call setup_provider_from_env() or set_provider()"
        )
    return _PROVIDER


# ── 公共数据结构 ─────────────────────────────────────

@dataclass(frozen=True)
class GeneratedImage:
    """单次图像生成的结果。"""
    bytes_data: bytes
    prompt: str
    aspect_ratio: str
    model: str


# ── 重试（与 llm.py 同构）────────────────────────────

_RETRY_BASE_SLEEP_S = 1.0
_RETRY_MAX_ATTEMPTS = 3


def _call_with_retry(
    provider: ImageProvider,
    prompt: str,
    *,
    aspect_ratio: str,
    n: int,
) -> list[bytes]:
    """指数退避 ×3，RetryableError 重试，其他异常立即抛。"""
    last_exc: Exception | None = None
    for attempt in range(1, _RETRY_MAX_ATTEMPTS + 1):
        try:
            return provider.call(
                prompt, aspect_ratio=aspect_ratio, n=n, response_format="base64"
            )
        except RetryableError as e:
            last_exc = e
            if attempt < _RETRY_MAX_ATTEMPTS:
                sleep_s = _RETRY_BASE_SLEEP_S * (2 ** (attempt - 1))
                time.sleep(sleep_s)
    assert last_exc is not None
    raise last_exc


# ── 落盘（tmp→rename 原子写 — 与 render.py 同构）──────

def _write_atomic(path: Path, data: bytes) -> None:
    """PNG/JPEG 原子写入：tmp → rename（HARD_PARTS §5 幂等）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    tmp.write_bytes(data)
    tmp.rename(path)


# ── 顶层入口：generate_image ───────────────────────────

def generate_image(
    prompt: str,
    *,
    out_path: Path,
    aspect_ratio: str = "1:1",
    n: int = 1,
    stage: str = "create_image",
    ref_id: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> GeneratedImage:
    """生成单张图像并落盘到 out_path（已 tmp→rename 原子写）。

    Args:
        prompt: 图像生成 prompt（英文/中文均可）
        out_path: 落盘目标路径（含文件名 + .png 后缀）
        aspect_ratio: '1:1' / '16:9' / '9:16' / '4:3' / '3:4'
        n: 生成数量（默认 1；当前 n>1 只取第一张）
        stage: 审计 stage 名（'create_cover' / 'create_image'）
        ref_id: 关联记录 id（content_id 等）
        conn: DB 连接；提供则写 llm_calls 审计 + 预算检查

    Returns:
        GeneratedImage 含 bytes_data（已落盘）+ prompt + aspect_ratio + model

    Raises:
        BudgetExceeded: 月度预算超限（按 estimate $0.003/张）
        RetryableError: 重试 3 次仍失败
        ValueError: 契约错误（参数错、响应残缺等）
    """
    provider = _require_provider()
    out_path = Path(out_path)

    # 预算检查（与 llm.py 同构：复用 _monthly_used_usd）
    if conn is not None:
        from pipeline.creators import llm as llm_mod  # 局部 import 避免循环
        model = getattr(provider, "_model", "image-01")
        now_dt = datetime.now(timezone.utc)
        used = llm_mod._monthly_used_usd(conn, now_dt)
        # image 按张计费：读 MODEL_PRICES["<model>"]["per_image_usd"]
        prices = llm_mod.MODEL_PRICES.get(model, {})
        per_image_usd = float(prices.get("per_image_usd", 0.0))
        if used + per_image_usd > llm_mod.BUDGET_LIMIT_USD:
            from pipeline.utils.errors import BudgetExceeded
            raise BudgetExceeded(
                stage=stage, used_usd=used, limit_usd=llm_mod.BUDGET_LIMIT_USD
            )

    # 调 provider（重试 3 次）
    images = _call_with_retry(
        provider, prompt, aspect_ratio=aspect_ratio, n=n
    )
    if not images:
        raise ValueError("image_gen: provider returned empty images list")

    # 落盘（取第一张；n>1 暂不支持——单次请求只生成 1 张以控成本）
    data = images[0]
    _write_atomic(out_path, data)

    # 审计：复用 llm_calls 表，stage='create_cover' / 'create_image'
    if conn is not None:
        from pipeline.creators import llm as llm_mod
        model = getattr(provider, "_model", "image-01")
        # image 模型按张计费，不是 token。绕过 _cost_usd 的 token 算式，
        # 直接读 MODEL_PRICES["<model>"]["per_image_usd"]（USD/张）。
        # 没配置则记 0（占位 + warning 由外层负责）。
        prices = llm_mod.MODEL_PRICES.get(model, {})
        per_image_usd = float(prices.get("per_image_usd", 0.0))
        result = llm_mod.CompletionResult(
            text=f"<image:{out_path.name}>",
            input_tokens=max(1, len(prompt) // 4),
            output_tokens=1,  # 1 张图
        )
        now_iso = datetime.now(timezone.utc).isoformat()
        llm_mod._record_llm_call(
            conn,
            stage=stage, ref_id=ref_id, model=model,
            result=result, cost_usd=per_image_usd, now=now_iso,
        )
        # 落 prompt + path 到 logs/llm/
        llm_mod._dump_log(
            stage=stage, ref_id=ref_id,
            prompt=prompt,
            response=f"saved to {out_path}",
            model=model, cost_usd=per_image_usd, now=now_iso,
        )

    return GeneratedImage(
        bytes_data=data,
        prompt=prompt,
        aspect_ratio=aspect_ratio,
        model=getattr(provider, "_model", "image-01"),
    )