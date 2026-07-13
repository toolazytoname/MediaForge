"""M10-5 settings router.

GET /api/v1/settings — config 脱敏展示 + doctor 报告合并

新增（Settings 页可用性改造）：
GET/POST/DELETE /api/v1/settings/keys — 全局服务 key（LLM/image-gen）的
查看/保存/清除，落盘到 `secrets/env.json`（见 `pipeline.env_keys`），
保存/清除后立即热重载 provider，不需要重启 webui 进程。
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from pipeline import doctor
from pipeline.env_keys import (
    DEFAULT_ENV_SECRETS_PATH,
    IMAGE_ENV_VARS,
    LLM_ENV_VARS,
    delete_env_secret,
    mask,
    write_env_secret,
)
from pipeline.webui import deps
from pipeline.webui.sanitize import sanitize_config

router = APIRouter(tags=["settings"])

# 落盘路径存成模块级变量（而非直接用函数默认参数），方便测试
# monkeypatch 到 tmp_path，不污染真实的 secrets/env.json。
_ENV_SECRETS_PATH = DEFAULT_ENV_SECRETS_PATH

# 白名单：只有这些 env var 名允许被 /settings/keys 端点写入/删除
# （防止端点被滥用成任意写文件）。
_KEY_GROUPS: list[tuple[str, str, tuple[str, ...]]] = [
    ("llm", "文本 LLM", LLM_ENV_VARS),
    ("image", "AI 出图", IMAGE_ENV_VARS),
]
_ALLOWED_KEY_NAMES = frozenset(LLM_ENV_VARS) | frozenset(IMAGE_ENV_VARS)


def _reload_providers() -> str | None:
    """保存/清除 key 后重新跑一遍 provider 初始化（热重载）。

    与 `webui/app.py::main()` 现有容错策略一致：llm 有 MockProvider 兜底
    不会抛；image_gen 没有兜底，key 缺失会直接 ValueError——AI 出图是可选
    功能，不应因为没配 key 就让这个端点整体报错。

    Returns:
        image_gen 初始化失败时的错误信息（供前端展示）；成功则 None。
    """
    from pipeline.creators import image_gen
    from pipeline.creators import llm as llm_mod

    llm_mod.setup_provider_from_env()
    try:
        image_gen.setup_provider_from_env()
    except Exception as e:
        return f"{type(e).__name__}: {e}"
    return None


@router.get("/settings")
def get_settings() -> dict[str, Any]:
    """脱敏 config + doctor 体检报告。"""
    cfg, err = deps.get_config()
    if cfg is None:
        return {
            "config": {},
            "config_error": err,
            "doctor": [],
        }
    sanitized = sanitize_config(cfg.model_dump())
    # doctor 用 cfg_path 报告
    try:
        report = doctor.run_doctor(deps._CONFIG_PATH)
    except Exception as e:
        # 兜底：手动造一个失败的 CheckResult
        from pipeline.doctor import CheckResult
        return {
            "config": sanitized,
            "doctor": [{"name": "doctor", "ok": False, "hint": f"doctor 失败：{e}"}],
        }
    return {
        "config": sanitized,
        "doctor": [
            {"name": r.name, "ok": r.ok, "hint": r.hint}
            for r in report
        ],
    }


# ── API Key 配置（Settings 页可用性改造） ──────────────────


@router.get("/settings/keys")
def get_keys() -> dict[str, Any]:
    """全局服务 key（LLM/image-gen）当前状态，按 group 分组，绝不回传明文。"""
    groups = []
    for group, label, names in _KEY_GROUPS:
        keys = []
        for name in names:
            value = os.environ.get(name)
            keys.append({
                "name": name,
                "set": bool(value),
                "masked": mask(value) if value else None,
            })
        groups.append({"group": group, "label": label, "keys": keys})
    return {"groups": groups}


@router.post("/settings/keys")
def save_key(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """保存一个 key：写 `secrets/env.json` + 立即热重载 provider。

    body: {"name": str, "value": str}
    → 200 + {name, set, masked, reload_error}
    → 400 unknown_key_name / empty_value
    """
    name = body.get("name")
    value = body.get("value")
    if not isinstance(name, str) or name not in _ALLOWED_KEY_NAMES:
        raise HTTPException(status_code=400, detail={"error": {
            "code": "unknown_key_name",
            "message": f"name must be one of {sorted(_ALLOWED_KEY_NAMES)}",
        }})
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(status_code=400, detail={"error": {
            "code": "empty_value", "message": "value must be a non-empty string",
        }})

    write_env_secret(name, value, _ENV_SECRETS_PATH)
    os.environ[name] = value
    reload_error = _reload_providers()
    return {"name": name, "set": True, "masked": mask(value), "reload_error": reload_error}


@router.delete("/settings/keys/{name}")
def clear_key(name: str) -> dict[str, Any]:
    """清除一个 key：删 `secrets/env.json` 里的条目 + 立即热重载 provider。

    → 200 + {name, set, reload_error}
    → 400 unknown_key_name
    """
    if name not in _ALLOWED_KEY_NAMES:
        raise HTTPException(status_code=400, detail={"error": {
            "code": "unknown_key_name",
            "message": f"name must be one of {sorted(_ALLOWED_KEY_NAMES)}",
        }})

    delete_env_secret(name, _ENV_SECRETS_PATH)
    os.environ.pop(name, None)
    reload_error = _reload_providers()
    return {"name": name, "set": False, "reload_error": reload_error}
