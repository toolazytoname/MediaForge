"""Local BYOK connection settings and the existing main WeChat account."""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Body, HTTPException

from pipeline.creators import llm
from pipeline.env_keys import atomic_write_secret, mask, update_env_secrets
from pipeline.publishers.wechat_mp import WechatMpPublisher, load_wechat_credentials
from pipeline.webui import deps
from pipeline.webui.api.settings import _reload_providers
from pipeline.webui.config_edit import set_publish_allowed_platforms, set_publish_enabled

router = APIRouter(tags=["settings"])
_ENV_PATH = Path("secrets/env.json")
_WECHAT_PATH = Path("secrets/wechat_mp_main.json")


def _bad(message: str) -> HTTPException:
    return HTTPException(400, detail={"error": {"code": "invalid_connection", "message": message}})


def _price(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise _bad("单价必须是非负有限数值")
    return str(value)


def _openai_defaults() -> tuple[str, str]:
    spec = llm.PROVIDER_SPECS["openai"]
    return spec.default_model, "responses"


@router.get("/settings/byok")
def get_byok() -> dict[str, Any]:
    default_model, default_wire = _openai_defaults()
    model = os.environ.get("OPENAI_MODEL", default_model)
    prices = [os.environ.get(key) for key in ("OPENAI_INPUT_PRICE", "OPENAI_OUTPUT_PRICE")]
    key = os.environ.get("OPENAI_API_KEY", "")
    user_priced = all(p is not None for p in prices)
    return {
        "base_url": os.environ.get("OPENAI_BASE_URL", llm.PROVIDER_SPECS["openai"].default_base_url),
        "text_model": model, "image_model": os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-2"),
        "wire_api": os.environ.get("OPENAI_WIRE_API", default_wire),
        "key_set": bool(key), "masked": mask(key) if key else None,
        "input_price": float(prices[0]) if prices[0] is not None else None,
        "output_price": float(prices[1]) if prices[1] is not None else None,
        "priced": user_priced or model in llm.MODEL_PRICES,
        "price_source": "user" if user_priced else ("official_estimate" if model in llm.MODEL_PRICES else "unpriced"),
        "cost_kind": "estimate",
    }


@router.put("/settings/byok")
def save_byok(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    allowed = {"base_url", "text_model", "image_model", "wire_api", "api_key", "input_price", "output_price"}
    if set(body) - allowed:
        raise _bad("未知配置字段")
    for field in ("base_url", "text_model", "image_model", "wire_api"):
        if not isinstance(body.get(field), str) or not body[field].strip():
            raise _bad(f"请填写 {field}")
    base_url = body["base_url"].strip().rstrip("/")
    url = urlsplit(base_url)
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise _bad("Base URL 必须是无凭据、查询参数或片段的 HTTP(S) 地址")
    if body["wire_api"] not in {"responses", "chat_completions"}:
        raise _bad("请选择 Responses 或 Chat Completions")
    api_key = body.get("api_key")
    if api_key is not None and (not isinstance(api_key, str) or not api_key.strip()):
        raise _bad("key 不能为空；留空时请省略该字段以保留现有 key")
    values = {
        "OPENAI_BASE_URL": base_url, "OPENAI_IMAGE_BASE_URL": base_url,
        "OPENAI_MODEL": body["text_model"].strip(), "OPENAI_IMAGE_MODEL": body["image_model"].strip(),
        "OPENAI_WIRE_API": body["wire_api"], "LLM_PROVIDER": "openai",
        "OPENAI_INPUT_PRICE": _price(body.get("input_price")),
        "OPENAI_OUTPUT_PRICE": _price(body.get("output_price")),
        "OPENAI_TIMEOUT_S": "120", "OPENAI_IMAGE_TIMEOUT_S": "120",
    }
    if api_key is not None:
        values["OPENAI_API_KEY"] = api_key.strip()
    update_env_secrets(values, _ENV_PATH)
    error = _reload_providers()
    return {**get_byok(), "reload_error": "缺少有效 key，请完成配置" if error else None}


@router.delete("/settings/byok/key")
def delete_byok_key() -> dict[str, Any]:
    update_env_secrets({"OPENAI_API_KEY": None}, _ENV_PATH)
    _reload_providers()
    return get_byok()


@router.post("/settings/byok/check")
def check_byok() -> dict[str, Any]:
    config = get_byok()
    if not config["key_set"]:
        return {
            "ok": False, "priced": config["priced"], "price_source": config["price_source"],
            "missing_models": [], "models": [], "message": "请先保存 API key",
        }
    try:
        response = httpx.get(config["base_url"] + "/models", headers={
            "Authorization": "Bearer " + os.environ["OPENAI_API_KEY"],
        }, timeout=15)
        response.raise_for_status()
        models = [item["id"] for item in response.json()["data"]]
    except Exception:
        return {
            "ok": False, "priced": config["priced"], "price_source": config["price_source"],
            "missing_models": [], "models": [],
            "message": "模型列表检查失败，请核对地址、key 和网络。",
        }
    missing = [name for name in (config["text_model"], config["image_model"]) if name not in models]
    if missing:
        message = f"模型列表未包含：{', '.join(missing)}"
    elif not config["priced"]:
        message = "模型列表可访问，但未配置估算单价，付费调用前会被阻止。真实写稿和出图仍需在项目中验证。"
    else:
        message = "模型列表可访问；真实写稿和出图仍需在项目中验证。所列单价仅为预算估算，不是中转实付。"
    return {
        "ok": not missing, "priced": config["priced"], "price_source": config["price_source"],
        "missing_models": missing, "models": models, "message": message,
    }


def _wechat_secret_path() -> Path:
    cfg, err = deps.get_config()
    try:
        platform = cfg.platforms.wechat_mp if cfg else None
        accounts = platform.accounts if platform else []
        if accounts and getattr(accounts[0], "credentials", None):
            return Path(accounts[0].credentials)
    except Exception:
        pass
    return _WECHAT_PATH


@router.get("/settings/wechat")
def get_wechat() -> dict[str, Any]:
    path = _wechat_secret_path()
    app_id = ""
    configured = False
    if path.is_file():
        try:
            app_id, secret = load_wechat_credentials(path)
            configured = bool(secret)
        except (ValueError, OSError):
            pass
    cfg, cfg_err = deps.get_config()
    enabled = bool(cfg and cfg.publish.enabled and "wechat_mp" in cfg.publish.allowed_platforms)
    mismatch = path != _WECHAT_PATH and not path.is_file() and _WECHAT_PATH.is_file()
    warning = None
    if cfg is None and cfg_err:
        warning = "无法读取本地配置，公众号凭据将写到默认 secrets/wechat_mp_main.json。"
    elif str(path) != str(_WECHAT_PATH):
        warning = f"当前发送方读取 {path}，设置页会保存到同一位置。"
    if mismatch:
        warning = f"配置引用 {path}，但该文件不存在；默认文件 {_WECHAT_PATH} 有凭据却不会被发送方读取。"
    return {
        "app_id": app_id, "configured": configured, "delivery_enabled": enabled,
        "credentials_path": str(path), "warning": warning,
    }


@router.put("/settings/wechat")
def save_wechat(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    if set(body) - {"app_id", "app_secret"}:
        raise _bad("未知公众号字段")
    app_id = body.get("app_id", "")
    if not isinstance(app_id, str) or not re.fullmatch(r"wx[a-zA-Z0-9]{16}", app_id):
        raise _bad("请填写有效的公众号 AppID")
    secret = body.get("app_secret")
    path = _wechat_secret_path()
    if secret is None and path.is_file():
        old_id, old_secret = load_wechat_credentials(path)
        secret = old_secret if old_id == app_id else None
    if not isinstance(secret, str) or not secret.strip():
        raise _bad("请填写 AppSecret")
    atomic_write_secret(_wechat_secret_path(), json.dumps({"app_id": app_id, "app_secret": secret.strip()}).encode())
    return get_wechat()


@router.post("/settings/wechat/check")
def check_wechat() -> dict[str, Any]:
    if not get_wechat()["configured"]:
        return {"ok": False, "message": "请先保存 AppID 和 AppSecret"}
    try:
        app_id, secret = load_wechat_credentials(_wechat_secret_path())
        provider = WechatMpPublisher(app_id=app_id, app_secret=secret)
        token = provider._ensure_access_token()
        response = httpx.get("https://api.weixin.qq.com/cgi-bin/draft/count", params={"access_token": token}, timeout=15)
        response.raise_for_status()
        data = response.json()
        if data.get("errcode"):
            return {"ok": False, "message": f"公众号草稿权限检查失败（{int(data['errcode'])}），请检查认证和接口权限。"}
        if "total_count" not in data:
            raise ValueError("missing draft count")
    except Exception as error:
        code = "40164" if "40164" in str(error) else None
        return {"ok": False, "message": "请将服务器出口 IP 加入公众号 IP 白名单（40164）。" if code else "连接失败，请核对 AppSecret、IP 白名单、接口权限和网络。"}
    return {"ok": True, "message": "凭据和草稿列表接口可用，实际写入权限将在送入草稿箱时验证。"}


@router.post("/settings/wechat/enable")
def enable_wechat() -> dict[str, Any]:
    if not get_wechat()["configured"]:
        raise _bad("请先填写公众号凭据")
    try:
        set_publish_allowed_platforms(["wechat_mp"], config_path=deps._CONFIG_PATH)
        set_publish_enabled(True, config_path=deps._CONFIG_PATH)
    except FileNotFoundError:
        raise _bad("未找到本地配置文件，无法启用公众号草稿交付") from None
    return get_wechat()
