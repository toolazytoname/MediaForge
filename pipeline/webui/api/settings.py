"""M10-5 settings router.

GET /api/v1/settings — config 脱敏展示 + doctor 报告合并

新增（Settings 页可用性改造）：
GET/POST/DELETE /api/v1/settings/keys — 全局服务 key（LLM/image-gen）的
查看/保存/清除，落盘到 `secrets/env.json`（见 `pipeline.env_keys`），
保存/清除后立即热重载 provider，不需要重启 webui 进程。

用户临场提需求（见 TASKS.md「待评估事项（用户临场提需求，2026-07-16）」）：
POST /api/v1/settings/publish-enabled — 写 config.yaml 的 publish.enabled
POST /api/v1/settings/publish-allowed-platforms — 写 publish.allowed_platforms
两者都是「发布总开关」——直接落盘到 config.yaml，不经过任何审批流程；
`deps.get_config()` 每次请求都重新读盘（无缓存），写完立即对下一次
safe_publish 门禁生效，无需重启 webui 进程。
"""
from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib import error, request
from urllib.parse import urlsplit

from fastapi import APIRouter, Body, HTTPException

from pipeline import doctor
from pipeline.config import PlatformsConfig
from pipeline.env_keys import (
    DEFAULT_ENV_SECRETS_PATH,
    IMAGE_ENV_VARS,
    LLM_ENV_VARS,
    delete_env_secret,
    mask,
    write_env_secret,
)
from pipeline.webui import deps
from pipeline.webui.config_edit import (
    set_publish_allowed_platforms,
    set_publish_enabled,
)
from pipeline.webui.sanitize import sanitize_config

router = APIRouter(tags=["settings"])


def _err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": {
        "code": code, "message": message,
    }})

# 落盘路径存成模块级变量（而非直接用函数默认参数），方便测试
# monkeypatch 到 tmp_path，不污染真实的 secrets/env.json。
_ENV_SECRETS_PATH = DEFAULT_ENV_SECRETS_PATH

# 白名单：只有这些 env var 名允许被 /settings/keys 端点写入/删除
# （防止端点被滥用成任意写文件）。
_KEY_GROUPS: list[tuple[str, str, tuple[str, ...]]] = [
    ("openai", "OpenAI（文本 / GPT Image 2）", ("OPENAI_API_KEY",)),
    ("llm", "其他文本 LLM", tuple(name for name in LLM_ENV_VARS if name != "OPENAI_API_KEY")),
    ("image", "其他 AI 出图", tuple(name for name in IMAGE_ENV_VARS if name != "OPENAI_API_KEY")),
]
_ALLOWED_KEY_NAMES = frozenset(LLM_ENV_VARS) | frozenset(IMAGE_ENV_VARS)
_OPENAI_IMAGE_BASE_URL = "OPENAI_IMAGE_BASE_URL"
_OPENAI_IMAGE_MODEL = "OPENAI_IMAGE_MODEL"
_OPENAI_IMAGE_DEFAULT_MODEL = "gpt-image-2"
_OPENAI_IMAGE_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


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


# ── GPT Image 2 中转站地址 ─────────────────────────────────


def _normalize_openai_image_base_url(value: Any) -> str:
    """Accept only an HTTPS OpenAI-compatible `/v1` API base URL."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("base_url must be non-empty text")
    try:
        parsed = urlsplit(value.strip())
    except ValueError as error:
        raise ValueError("base_url must be a valid HTTPS URL") from error
    path = parsed.path.rstrip("/")
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or path != "/v1"
    ):
        raise ValueError("base_url must be an HTTPS OpenAI-compatible /v1 URL")
    return f"https://{parsed.netloc}/v1"


@router.get("/settings/openai-image-base-url")
def get_openai_image_base_url() -> dict[str, str | None]:
    """Return the configured relay endpoint; it is not a credential."""
    return {"base_url": os.environ.get(_OPENAI_IMAGE_BASE_URL) or None}


@router.post("/settings/openai-image-base-url")
def save_openai_image_base_url(body: dict[str, Any] = Body(...)) -> dict[str, str | None]:
    """Persist and hot-reload a GPT Image 2 OpenAI-compatible relay endpoint."""
    if set(body) != {"base_url"}:
        raise _err(400, "invalid_openai_image_base_url", "request requires only base_url")
    try:
        base_url = _normalize_openai_image_base_url(body["base_url"])
    except ValueError as error:
        raise _err(400, "invalid_openai_image_base_url", str(error)) from error
    write_env_secret(_OPENAI_IMAGE_BASE_URL, base_url, _ENV_SECRETS_PATH)
    os.environ[_OPENAI_IMAGE_BASE_URL] = base_url
    return {"base_url": base_url, "reload_error": _reload_providers()}


@router.delete("/settings/openai-image-base-url")
def clear_openai_image_base_url() -> dict[str, str | None]:
    """Clear the persisted relay endpoint and return GPT Image 2 to its default."""
    delete_env_secret(_OPENAI_IMAGE_BASE_URL, _ENV_SECRETS_PATH)
    os.environ.pop(_OPENAI_IMAGE_BASE_URL, None)
    return {"base_url": None, "reload_error": _reload_providers()}


# ── GPT Image 2 中转站模型 ─────────────────────────────────


def _normalize_openai_image_model(value: Any) -> str:
    """Accept a bounded OpenAI-compatible model identifier, never a path."""
    if not isinstance(value, str) or not _OPENAI_IMAGE_MODEL_RE.fullmatch(value):
        raise ValueError(
            "model must be a 1–128 character identifier using letters, numbers, dots, underscores, or hyphens"
        )
    return value


@router.get("/settings/openai-image-model")
def get_openai_image_model() -> dict[str, Any]:
    """Return the active model name without exposing any credential."""
    configured = os.environ.get(_OPENAI_IMAGE_MODEL)
    return {"model": configured or _OPENAI_IMAGE_DEFAULT_MODEL, "configured": bool(configured)}


@router.post("/settings/openai-image-model")
def save_openai_image_model(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Persist and hot-reload a relay-supported Images API model name."""
    if set(body) != {"model"}:
        raise _err(400, "invalid_openai_image_model", "request requires only model")
    try:
        model = _normalize_openai_image_model(body["model"])
    except ValueError as error:
        raise _err(400, "invalid_openai_image_model", str(error)) from error
    write_env_secret(_OPENAI_IMAGE_MODEL, model, _ENV_SECRETS_PATH)
    os.environ[_OPENAI_IMAGE_MODEL] = model
    return {"model": model, "configured": True, "reload_error": _reload_providers()}


@router.delete("/settings/openai-image-model")
def clear_openai_image_model() -> dict[str, Any]:
    """Clear the relay override and use the built-in GPT Image 2 default."""
    delete_env_secret(_OPENAI_IMAGE_MODEL, _ENV_SECRETS_PATH)
    os.environ.pop(_OPENAI_IMAGE_MODEL, None)
    return {
        "model": _OPENAI_IMAGE_DEFAULT_MODEL,
        "configured": False,
        "reload_error": _reload_providers(),
    }


@router.get("/settings/openai-image-models")
def discover_openai_image_models() -> dict[str, Any]:
    """Read a configured compatible relay's model catalogue without saving it.

    This is deliberately a read-only settings aid.  It does not attempt image
    generation, does not alter the active model, and never returns the relay
    URL, request headers, or upstream error body.
    """
    base_url = os.environ.get(_OPENAI_IMAGE_BASE_URL)
    api_key = os.environ.get("OPENAI_API_KEY")
    if not base_url or not api_key:
        raise _err(409, "image_model_discovery_unavailable", "configure an OpenAI-compatible image relay and key first")
    try:
        # Stored settings already pass this normalizer, but re-validating before
        # a network call ensures environment tampering cannot turn this into an
        # arbitrary request primitive.
        normalized = _normalize_openai_image_base_url(base_url)
        req = request.Request(f"{normalized}/models", headers={"Authorization": f"Bearer {api_key}"}, method="GET")
        with request.urlopen(req, timeout=8.0) as response:
            payload = json.loads(response.read())
    except (ValueError, error.HTTPError, error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        # Only report a stable local reason.  Compatible endpoints sometimes
        # echo authorization fragments in their response bodies.
        raise _err(502, "image_model_discovery_failed", f"could not read the relay model list ({type(exc).__name__})") from exc
    items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise _err(502, "image_model_discovery_failed", "relay returned an invalid model list")
    models = sorted({item["id"] for item in items if isinstance(item, dict) and isinstance(item.get("id"), str) and _OPENAI_IMAGE_MODEL_RE.fullmatch(item["id"])})
    return {"models": models, "source": "relay"}


# ── publish.enabled / allowed_platforms（发布总开关，用户明确要求
# 能从 UI 操作，不必手改 config.yaml——见 TASKS.md「待评估事项（用户
# 临场提需求，2026-07-16）」） ───────────────────────────────


@router.post("/settings/publish-enabled")
def set_publish_enabled_endpoint(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """写 config.yaml 的 `publish.enabled`——发布总开关。

    body: {"enabled": bool}
    → 200 {"enabled": bool}
    → 400 invalid_enabled（非 bool）
    """
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail={"error": {
            "code": "invalid_enabled", "message": "enabled must be a boolean",
        }})
    result = set_publish_enabled(enabled)
    return {"enabled": result}


@router.post("/settings/publish-allowed-platforms")
def set_publish_allowed_platforms_endpoint(
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """写 config.yaml 的 `publish.allowed_platforms`（整体覆盖）。

    body: {"platforms": list[str]}
    → 200 {"platforms": list[str]}
    → 400 invalid_platforms（非 list[str]）/ unknown_platform（含未知 platform key）
    """
    platforms = body.get("platforms")
    if not isinstance(platforms, list) or not all(
        isinstance(p, str) for p in platforms
    ):
        raise HTTPException(status_code=400, detail={"error": {
            "code": "invalid_platforms", "message": "platforms must be a list of strings",
        }})
    known = set(PlatformsConfig.model_fields.keys())
    unknown = [p for p in platforms if p not in known]
    if unknown:
        raise HTTPException(status_code=400, detail={"error": {
            "code": "unknown_platform",
            "message": f"unknown platform(s) {unknown}; known: {sorted(known)}",
        }})
    result = set_publish_allowed_platforms(platforms)
    return {"platforms": result}
