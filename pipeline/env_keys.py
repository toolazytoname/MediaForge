"""全局服务 key（LLM / image-gen）的共享名单与持久化。

背景：`doctor.py`、`webui/app.py`、`webui/api/settings.py` 都需要同一份
「已知 env var 名单」，此前 `doctor.py` 本地定义 `_LLM_ENV_VARS`/
`_IMAGE_ENV_VARS`，其余模块各自读 `os.environ`，没有统一的持久化。

这里补一个持久化层：`secrets/env.json`（纯 JSON，风格与
`secrets/x_<account>.json`/`secrets/wechat_mp_<account>.json` 一致），
配合 `load_env_secrets()` 在进程启动早期把它合并进 `os.environ`，之后
`setup_provider_from_env()` 系列函数无需任何改动就能读到。

绝不打印/回传明文 key 值（HARD_PARTS §9），对外只暴露 `mask()` 结果。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# 与 llm.py::setup_provider_from_env 优先级链同源
LLM_ENV_VARS = ("AGNES_API_KEY", "MINIMAX_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY")

# 与 image_gen.py::MiniMaxImageProvider.from_env 优先级链同源
IMAGE_ENV_VARS = ("MINIMAX_IMAGE_API_KEY", "MINIMAX_API_KEY")

DEFAULT_ENV_SECRETS_PATH = "secrets/env.json"


def _read(path: str | Path) -> dict[str, str]:
    p = Path(path)
    if not p.exists():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8") or "{}")
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def _write(path: str | Path, data: dict[str, str]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_env_secrets(path: str | Path = DEFAULT_ENV_SECRETS_PATH) -> None:
    """把 `secrets/env.json` 里的 key 合并进 `os.environ`。

    文件不存在 → no-op。已存在的真实进程 env 优先，不覆盖（与 dotenv 惯例
    一致，避免这层新机制在生产环境意外覆盖运维已设置的值）。
    """
    for name, value in _read(path).items():
        os.environ.setdefault(name, value)


def write_env_secret(name: str, value: str, path: str | Path = DEFAULT_ENV_SECRETS_PATH) -> None:
    """写入/更新一个 key 到 `secrets/env.json`（不改动其它已存 key）。"""
    data = _read(path)
    data[name] = value
    _write(path, data)


def delete_env_secret(name: str, path: str | Path = DEFAULT_ENV_SECRETS_PATH) -> bool:
    """从 `secrets/env.json` 删除一个 key。

    Returns:
        True 表示确实删了一条；False 表示文件不存在或该 key 本就不在里面
        （幂等 no-op，不算错误）。
    """
    data = _read(path)
    if name not in data:
        return False
    del data[name]
    _write(path, data)
    return True


def mask(value: str) -> str:
    """只留末 4 位，其余替换为 `*`（绝不回传明文）。"""
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * (len(value) - 4) + value[-4:]


__all__ = [
    "LLM_ENV_VARS",
    "IMAGE_ENV_VARS",
    "DEFAULT_ENV_SECRETS_PATH",
    "load_env_secrets",
    "write_env_secret",
    "delete_env_secret",
    "mask",
]
