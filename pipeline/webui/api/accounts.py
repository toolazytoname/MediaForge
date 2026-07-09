"""M10-5 accounts router.

GET /api/v1/accounts — 账号 + cookie 健康状态
GET /api/v1/accounts/login-guidance — 每平台登录指引（静态）
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from pipeline.webui import deps
from pipeline.webui.cookie_health_views import collect_cookie_health

router = APIRouter(tags=["accounts"])


@router.get("/accounts")
def list_accounts() -> dict[str, Any]:
    cfg, err = deps.get_config()
    if cfg is None:
        return {"items": [], "config_error": err}
    items = collect_cookie_health(cfg)
    # CookieHealthItem → dict
    out = []
    for it in items:
        out.append({
            "platform": it.platform,
            "account": it.account,
            "healthy": it.healthy,
            "detail": it.detail,
            "last_check_at": it.last_check_at,
        })
    return {"items": out}


@router.get("/accounts/login-guidance")
def login_guidance() -> dict[str, Any]:
    """每平台登录引导（静态文案）。"""
    return {
        "items": [
            {
                "platform": "toutiao",
                "command": "python -m pipeline.run login toutiao main",
                "notes": "扫码登录头条创作者后台；cookie 存 secrets/",
            },
            {
                "platform": "xiaohongshu",
                "command": "python -m pipeline.run login xiaohongshu main",
                "notes": "扫码登录小红书；通过 XiaohongshuSkills 的 cdp_publish.py 完成",
            },
            {
                "platform": "x",
                "command": "(env: X_BEARER_TOKEN=...)",
                "notes": "X 用 API v2 OAuth bearer token；非扫码路径",
            },
            {
                "platform": "douyin",
                "command": "python -m pipeline.run login douyin main",
                "notes": "扫码登录抖音创作者后台；Playwright + storage_state",
            },
        ]
    }
