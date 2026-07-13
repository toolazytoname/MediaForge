"""上线前自检（doctor / S8-3）。

只读体检命令——告诉用户「现在缺什么才能真跑起来」。
- **绝不创建任何文件**（不建 db / secrets/ / 不改 config / 不写日志文件）
- **绝不打印密钥值**（HARD_PARTS §9 凭据安全）
- **绝不调 LLM**（避免网络抖动误报）

检查项（顺序固定，7 项）：
  1. config       — config.yaml 存在 + load_config 通过
  2. state.db     — state.db 文件存在
  3. secrets      — secrets/ 目录存在（空目录也算过）
  4. llm_key      — AGNES_API_KEY / MINIMAX_API_KEY / ANTHROPIC_API_KEY
                   / OPENAI_API_KEY 至少一个（与 setup_provider_from_env 优先级一致）
  5. budget       — config.budget.monthly_usd > 0
  6. publish.enabled — 当前值（true 提示「⚠️ 真发」，不 fail 只 warn）
  7. image_key    — MINIMAX_IMAGE_API_KEY / MINIMAX_API_KEY 是否设置（与
                   image_gen.MiniMaxImageProvider.from_env 优先级一致）；AI 出图是
                   可选功能，未设置不 fail，只在 hint 里提示（不影响 exit code）
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from pipeline.config import load_config
from pipeline.env_keys import IMAGE_ENV_VARS as _IMAGE_ENV_VARS
from pipeline.env_keys import LLM_ENV_VARS as _LLM_ENV_VARS


@dataclass(frozen=True)
class CheckResult:
    """单项检查结果（frozen 不可变，遵守全局 coding-style 规则）。"""
    name: str
    ok: bool
    hint: str  # 失败时含修复建议；通过时含简要说明


def _check_config(config_path: str) -> CheckResult:
    """config.yaml 存在 + load_config 通过（pydantic ValidationError 兜住）。"""
    p = Path(config_path)
    if not p.exists():
        return CheckResult(
            name="config",
            ok=False,
            hint=(
                f"未找到 {config_path}；"
                "请运行 `cp config.example.yaml config.yaml` 然后按需修改"
            ),
        )
    try:
        load_config(config_path)
    except FileNotFoundError:
        # 与上面 p.exists() 重复兜一次（防 TOCTOU）
        return CheckResult(
            name="config",
            ok=False,
            hint=f"未找到 {config_path}；请 `cp config.example.yaml config.yaml`",
        )
    except (ValidationError, ValueError) as e:
        # pydantic 校验失败 / 空文件 / 字段类型错
        # 注：load_config 把 ValidationError 包装成 ValueError，两者都接
        err_msg = str(e).strip().splitlines()[0] if str(e).strip() else "未知错误"
        return CheckResult(
            name="config",
            ok=False,
            hint=f"config 校验失败：{err_msg}",
        )
    return CheckResult(
        name="config",
        ok=True,
        hint=f"已加载 {config_path}",
    )


def _check_state_db(db_path: str) -> CheckResult:
    """state.db 文件存在。"""
    p = Path(db_path)
    if not p.exists():
        return CheckResult(
            name="state.db",
            ok=False,
            hint=f"未找到 {db_path}；请运行 `python -m pipeline.run init-db`",
        )
    return CheckResult(
        name="state.db",
        ok=True,
        hint=f"已就绪 ({db_path})",
    )


def _check_secrets(secrets_dir: str) -> CheckResult:
    """secrets/ 目录存在（空目录也算过——本任务只验目录，不验具体凭据）。"""
    p = Path(secrets_dir)
    if not p.is_dir():
        return CheckResult(
            name="secrets",
            ok=False,
            hint=(
                f"未找到 {secrets_dir} 目录；"
                "请运行 `mkdir -p secrets`（这是平台 cookie 等凭据存放地）"
            ),
        )
    return CheckResult(
        name="secrets",
        ok=True,
        hint=f"已就绪 ({secrets_dir})",
    )


def _check_llm_key() -> CheckResult:
    """env 至少设置 AGNES/MINIMAX/ANTHROPIC/OPENAI_API_KEY 之一（绝不打印值）。"""
    set_vars = [v for v in _LLM_ENV_VARS if os.environ.get(v)]
    if not set_vars:
        return CheckResult(
            name="llm_key",
            ok=False,
            hint=(
                "未设置 LLM API key；请 export AGNES_API_KEY=<your-key> "
                "或 MINIMAX_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY"
            ),
        )
    # 不打印具体值，只报「已设置哪些 env var」
    return CheckResult(
        name="llm_key",
        ok=True,
        hint=f"已设置 {', '.join(set_vars)}（值未显示）",
    )


def _check_budget(cfg: Any | None) -> CheckResult:
    """config.budget.monthly_usd > 0。"""
    if cfg is None:
        # config 加载失败时不重复报「缺 budget」，让 config 检查承担
        return CheckResult(
            name="budget",
            ok=False,
            hint="因 config 校验失败无法检查 budget；先修 config",
        )
    monthly = float(cfg.budget.monthly_usd)
    if monthly <= 0:
        return CheckResult(
            name="budget",
            ok=False,
            hint=(
                f"budget.monthly_usd={monthly}，预算被禁用；"
                "建议在 config.yaml 中设 >0（如 80.0）"
            ),
        )
    return CheckResult(
        name="budget",
        ok=True,
        hint=f"已设置 ${monthly:.2f}/月",
    )


def _check_publish_enabled(cfg: Any | None) -> CheckResult:
    """config.publish.enabled 当前值（true 提示真发；不 fail 只 warn-style hint）。"""
    if cfg is None:
        return CheckResult(
            name="publish.enabled",
            ok=False,
            hint="因 config 校验失败无法检查 publish；先修 config",
        )
    enabled = bool(cfg.publish.enabled)
    if enabled:
        return CheckResult(
            name="publish.enabled",
            ok=True,
            hint="⚠️ publish.enabled=true：发布总闸已开，会真发（不当作 fail）",
        )
    return CheckResult(
        name="publish.enabled",
        ok=True,
        hint="publish.enabled=false：发布总闸关闭，安全",
    )


def _check_image_key() -> CheckResult:
    """env 是否设置 image_gen 专用 key（不打印值）。

    AI 出图是可选功能（HARD_PARTS/app.py::main() 明确不应因缺 key 拖垮整个服务），
    所以本项永远 ok=True，只通过 hint 提示是否已配置——不影响 doctor 整体 exit code。
    """
    set_vars = [v for v in _IMAGE_ENV_VARS if os.environ.get(v)]
    if not set_vars:
        return CheckResult(
            name="image_key",
            ok=True,
            hint=(
                "⚠️ 未设置 image provider key；AI 出图功能不可用（可选功能，不影响其余"
                "流程）。如需使用请 export MINIMAX_IMAGE_API_KEY=<key>（或 MINIMAX_API_KEY）"
            ),
        )
    return CheckResult(
        name="image_key",
        ok=True,
        hint=f"已设置 {', '.join(set_vars)}（值未显示）",
    )


def run_doctor(
    config_path: str = "./config.yaml",
    db_path: str = "state.db",
    secrets_dir: str = "secrets",
) -> list[CheckResult]:
    """体检主入口（纯函数，便于测试）。

    Returns:
        按 spec 固定顺序的 7 项 CheckResult。
        调用方根据是否 ok 决定 exit code。
    """
    # 先跑 config 检查（其它项可能依赖 cfg）
    config_result = _check_config(config_path)

    # 尝试加载 config（即便 config ❌，也不阻断其它项——体检可独立报问题）
    cfg: Any | None = None
    if config_result.ok:
        try:
            cfg = load_config(config_path)
        except Exception:  # noqa: BLE001  # 已被 config_result 捕获，这里再兜
            cfg = None

    return [
        config_result,
        _check_state_db(db_path),
        _check_secrets(secrets_dir),
        _check_llm_key(),
        _check_budget(cfg),
        _check_publish_enabled(cfg),
        _check_image_key(),
    ]
