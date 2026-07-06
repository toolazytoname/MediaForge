"""Cookie 健康检测（HARD_PARTS §2 决策 2）。

发布前先用 cookie 访问个人主页做轻量校验；失效 → LoginExpired，
编排层（safe_publish）会让该平台所有任务停止 + 告警，**不会带着失效
cookie 反复撞平台触发风控**。

健康检测仅校验 cookie 是否被平台接受；不校验内容是否能成功发布
（那是 publish 阶段的事）。

实现要点：
1. 缓存：单次编排周期内 (process) 同一账号只查一次；避免重复访问
   个人主页（每次都重网络）。
2. 失败语义：网络错误 ≠ cookie 失效；只有「到达平台 + 跳登录页」
   才判失效。其他情况抛 PublishError 让编排层决定 retry 还是 fail。
3. X 走 OAuth2 无 cookie 概念，**不参与此模块**。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pipeline.publishers.base import LoginExpired, PublishError


@dataclass(frozen=True)
class CookieHealth:
    """单次健康检测结果。"""
    healthy: bool
    detail: str  # 成功原因 / 失败证据（URL / 命中关键词）


def load_storage_state(path: str | Path) -> dict:
    """读取 Playwright storage_state JSON → dict。

    校验文件存在 + 是合法 JSON + 至少有 cookies/origins 之一；
    格式错抛 PublishError（凭据损坏 = 编排层不能继续）。
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"storage_state file not found: {p} "
            f"(run `python -m pipeline.run login <platform> <account>`)"
        )
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise PublishError(
            f"storage_state file unreadable/invalid JSON: {p}: {e}"
        ) from e
    if not isinstance(raw, dict):
        raise PublishError(
            f"storage_state root must be dict, got {type(raw).__name__}: {p}"
        )
    has_cookies = isinstance(raw.get("cookies"), list)
    has_origins = isinstance(raw.get("origins"), list)
    if not (has_cookies or has_origins):
        raise PublishError(
            f"storage_state has no cookies/origins (empty login?): {p}"
        )
    return raw


def check_health(
    platform: str,
    account_id: str,
    *,
    storage_state_path: Path,
    login_indicators: tuple[str, ...],
    profile_urls: tuple[str, ...],
    probe_page,  # (storage_state_path: Path, urls: tuple[str, ...]) -> tuple[int, str, str]
    #              → (status_code, final_url, page_text_summary)
    screenshot_dir: Path | None = None,
) -> CookieHealth:
    """执行健康检测（无副作用、不写 DB、不调平台写接口）。

    Args:
        platform: 'toutiao' | 'xiaohongshu'
        account_id: 账号别名
        storage_state_path: Playwright storage_state JSON 路径
        login_indicators: 登录页典型文本（任一命中 = 失效）
        profile_urls: 个人主页候选 URL（按顺序尝试）
        probe_page: 探测函数（注入：测试 fake / 生产 Playwright）
        screenshot_dir: 截图保存目录（调试用）

    Returns:
        CookieHealth(healthy, detail)

    Raises:
        LoginExpired: cookie 失效
        PublishError: 网络/IO 等其他故障
    """
    # 1. 先校验 storage_state 文件本身
    load_storage_state(storage_state_path)

    # 2. 用注入的 probe 探活
    try:
        status, final_url, page_text = probe_page(
            storage_state_path, profile_urls,
        )
    except LoginExpired:
        # LoginExpired 信号直达编排层（停止该平台 + 告警）— 不包装
        raise
    except Exception as e:
        raise PublishError(
            f"{platform}/{account_id} cookie health probe failed: {e!r}"
        ) from e

    # 3. 判定
    # HTTP 2xx + URL 不在登录路径 + 页面不含登录关键词 = 健康
    if 200 <= status < 300:
        # 登录路径命中（按 substring）→ 失效
        login_url_keywords = ("login", "auth", "passport")
        if any(kw in final_url.lower() for kw in login_url_keywords):
            raise LoginExpired(
                f"{platform}/{account_id} cookie expired: "
                f"redirected to {final_url!r}"
            )
        if any(ind in page_text for ind in login_indicators):
            raise LoginExpired(
                f"{platform}/{account_id} cookie expired: "
                f"login indicators found in page ({final_url!r})"
            )
        return CookieHealth(
            healthy=True,
            detail=f"status={status} url={final_url}",
        )

    # 4xx → 大概率未授权 = cookie 失效
    if 400 <= status < 500:
        raise LoginExpired(
            f"{platform}/{account_id} cookie expired: "
            f"HTTP {status} at {final_url!r}"
        )

    # 5xx / 网络异常 = 上游故障 ≠ cookie 失效；让编排层决定
    raise PublishError(
        f"{platform}/{account_id} cookie health probe HTTP {status} "
        f"(not login_expired; upstream issue)"
    )


__all__ = [
    "CookieHealth",
    "load_storage_state",
    "check_health",
]