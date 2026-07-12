"""M10-5 accounts router.

GET /api/v1/accounts — 账号 + cookie 健康状态
GET /api/v1/accounts/login-guidance — 每平台登录指引（静态）

U7-7:
POST /api/v1/accounts/{platform}/{account}/login — Web UI 一键登录触发端点
  - 校验 platform ∈ {toutiao,xiaohongshu,douyin}（白名单）
  - 一键登录的 mutex / 后台任务 / progress 监听全部由
    `pipeline.webui.login_bridge` 负责（accounts.py 不直接 import
    run_login / PublishError / execute_login_run，避免循环 import 风险）
  - 前端轮询 `GET /api/v1/runs/{run_id}` 拿实时进度（消息来自 R7-7
    log_event 链路）
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException

from pipeline.webui import deps
from pipeline.webui.config_edit import remove_account_from_config
from pipeline.webui.cookie_health_views import collect_cookie_health
from pipeline.webui.login_bridge import (
    LoginInProgressError,
    delete_login_credentials,
    is_login_in_progress,
    new_login_run_id,
    start_login_background,
)

router = APIRouter(tags=["accounts"])

_LOGGER = logging.getLogger("pipeline.webui.api.accounts")


# Web UI 支持的一键登录平台白名单（与 _PLATFORM_LOGIN_DISPATCH 一致）
# x / wechat_mp 走配置文件，不属于扫码登录流程
_SUPPORTED_WEB_LOGIN = frozenset({"toutiao", "xiaohongshu", "douyin"})


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
                "auth_type": "scan_qr",
                "command": "python -m pipeline.run login toutiao main",
                "notes": "扫码登录头条创作者后台；cookie 存 secrets/",
            },
            {
                "platform": "xiaohongshu",
                "auth_type": "scan_qr",
                "command": "python -m pipeline.run login xiaohongshu main",
                "notes": "扫码登录小红书；通过 XiaohongshuSkills 的 cdp_publish.py 完成",
            },
            {
                "platform": "x",
                "auth_type": "config_file",
                "command": "(env: X_BEARER_TOKEN=...)",
                "notes": "X 用 API v2 OAuth bearer token；非扫码路径",
            },
            {
                "platform": "douyin",
                "auth_type": "scan_qr",
                "command": "python -m pipeline.run login douyin main",
                "notes": "扫码登录抖音创作者后台；Playwright + storage_state",
            },
            {
                "platform": "wechat_mp",
                "auth_type": "config_file",
                "command": "secrets/wechat_mp_<account>.json",
                "notes": (
                    "无扫码流程；在 config.yaml 配置 platforms.wechat_mp.accounts[]."
                    "credentials 指向该文件（内容 {\"app_id\":..., \"app_secret\":...}），"
                    "并在公众号后台设置与开发→基本配置→IP 白名单加入服务器出口 IP"
                    "（否则 access_token 请求返回 errcode 40164）"
                ),
            },
        ]
    }


# ── U7-7: Web UI 一键登录端点 ────────────────────────────────


@router.post(
    "/accounts/{platform}/{account}/login",
    status_code=202,
)
def start_login(
    platform: str,
    account: str,
    background: BackgroundTasks,
) -> dict[str, Any]:
    """Web UI 触发登录：BackgroundTasks 跑 run_login，前端轮询
    `GET /api/v1/runs/{run_id}` 看实时进度（消息来自 R7-7 log_event 链路）。

    Returns:
        202 + `{run_id, status="queued", platform, account}`

    Errors:
        400 platform_not_supported — platform 不在白名单（x / wechat_mp 等）
        409 login_in_progress — 同账号已有运行中的 run

    Notes:
        - 立即返回 202；不阻塞等待 login 完成。
        - 进度消息通过 `login_cmd.add_progress_listener` 实时透传到 runs
          registry（见 `pipeline.webui.login_bridge.execute_login_run`）。
        - 互斥 / 后台任务编排全部在 `login_bridge.start_login_background`；
          本路由只负责 platform 白名单校验 + 错误码映射。
    """
    if platform not in _SUPPORTED_WEB_LOGIN:
        raise HTTPException(status_code=400, detail={"error": {
            "code": "platform_not_supported",
            "message": (
                f"platform {platform!r} not supported for web login; "
                f"supported: {sorted(_SUPPORTED_WEB_LOGIN)}"
            ),
        }})

    run_id = new_login_run_id()
    try:
        start_login_background(run_id, platform, account, background)
    except LoginInProgressError as e:
        raise HTTPException(status_code=409, detail={"error": {
            "code": "login_in_progress",
            "message": (
                f"login already running for {platform}/{account}; "
                f"run_id={e.existing_run_id}"
            ),
        }})

    return {
        "run_id": run_id,
        "status": "queued",
        "platform": platform,
        "account": account,
    }


# ── U7-8/U7-9: 删除账号（凭据文件 + config.yaml 账号条目） ──────


@router.delete("/accounts/{platform}/{account}/login")
def delete_login(platform: str, account: str) -> dict[str, Any]:
    """彻底移除一个账号：删凭据文件 + 从 config.yaml 里删账号条目。

    U7-8 最初只删凭据文件，账号仍留在 config.yaml、UI 上账号行不消失、
    只是健康状态变红——用户实际用过之后明确要求"彻底移除账号"（U7-9
    用户决策，见 TASKS.md）：删除后账号应该从列表整个消失，才符合直觉。
    账号从 config.yaml 消失后，`collect_cookie_health` 遍历
    config-declared 账号时自然不再返回它，前端 `load()` 刷新即可让
    该行从列表消失，不需要额外的前端过滤逻辑。

    Returns:
        200 + `{deleted: bool, platform, account}`
        deleted 反映"凭据文件被删除 或 config.yaml 账号条目被删除"——
        两者只要有一个发生就算真的做了事；两者都没发生（幂等重复删除）
        才是 False。

    Errors:
        400 platform_not_supported — platform 不在白名单
        409 login_in_progress — 同账号有运行中的登录 run（避免跟正在
            写入的 cookie 文件产生竞态）
    """
    if platform not in _SUPPORTED_WEB_LOGIN:
        raise HTTPException(status_code=400, detail={"error": {
            "code": "platform_not_supported",
            "message": (
                f"platform {platform!r} not supported for web login; "
                f"supported: {sorted(_SUPPORTED_WEB_LOGIN)}"
            ),
        }})

    running_run_id = is_login_in_progress(platform, account)
    if running_run_id is not None:
        raise HTTPException(status_code=409, detail={"error": {
            "code": "login_in_progress",
            "message": (
                f"login already running for {platform}/{account}; "
                f"run_id={running_run_id}"
            ),
        }})

    credentials_deleted = delete_login_credentials(platform, account)
    config_removed = remove_account_from_config(platform, account)
    return {
        "deleted": credentials_deleted or config_removed,
        "platform": platform,
        "account": account,
    }