"""Cookie 健康视图工具（M4-4 settings 页）。

读 cfg.platforms 启用的平台账号，列出每个账号的 cookie / 登录态状态：
- 头条：检查 `secrets/cookies/toutiao_<account>.json` 存在 + 合法 JSON
- 小红书：检查 `XHS_SKILLS_PATH` / config 路径下的 `scripts/cdp_publish.py` + `scripts/publish_pipeline.py` 都存在
- X：检查 `secrets/x_<account>.json` 含 bearer_token

**轻量级**：不实际探活（避免 settings 页 hang 住）；只看文件存在 + 格式合法。
真实健康检测走 `pipeline.publishers.cookie_health.check_health`（publish 时）。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CookieHealthItem:
    """单平台单账号的 cookie 状态。"""
    platform: str
    account: str
    healthy: bool
    detail: str
    last_check_at: str  # 当前时间（轻量级不探活，记录「最近一次 settings 访问」）


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _check_xhs_skills_path(config: Any) -> tuple[bool, str]:
    """小红书 skills 路径校验：CLI 文件齐 + 路径存在。"""
    plat = getattr(config.platforms, "xiaohongshu", None)
    if plat is None:
        return (False, "platform not configured")
    skills = plat.skills_path if hasattr(plat, "skills_path") else None
    skills = skills or os.environ.get("XHS_SKILLS_PATH") or "~/.agents/skills/xiaohongshu-skills"
    p = Path(skills).expanduser()
    if not p.exists():
        return (False, f"skills dir not found: {p}")
    cli_pub = p / "scripts" / "publish_pipeline.py"
    cli_login = p / "scripts" / "cdp_publish.py"
    missing = [str(s) for s in (cli_pub, cli_login) if not s.exists()]
    if missing:
        return (False, f"missing: {missing}")
    return (True, f"path={p}")


def _check_storage_state(path: Path) -> tuple[bool, str]:
    """Playwright storage_state JSON 校验。"""
    from pipeline.publishers.cookie_health import load_storage_state
    if not path.exists():
        return (False, f"file not found: {path}")
    try:
        state = load_storage_state(path)
    except Exception as e:
        return (False, f"invalid: {e}")
    n_cookies = len(state.get("cookies", []))
    n_origins = len(state.get("origins", []))
    return (True, f"cookies={n_cookies}, origins={n_origins}")


def _check_x_credentials(path: Path) -> tuple[bool, str]:
    """X API bearer_token JSON 校验。"""
    if not path.exists():
        return (False, f"file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return (False, f"invalid JSON: {e}")
    tok = data.get("bearer_token") if isinstance(data, dict) else None
    if not isinstance(tok, str) or not tok:
        return (False, "missing bearer_token")
    return (True, f"bearer_token len={len(tok)}")


def collect_cookie_health(config: Any) -> list[CookieHealthItem]:
    """遍历 cfg.platforms 全部启用平台 + 账号，返回健康项列表。"""
    now = _now_iso()
    out: list[CookieHealthItem] = []

    # 头条
    plat = getattr(config.platforms, "toutiao", None)
    if plat is not None and plat.accounts:
        for acc in plat.accounts:
            cookies_field = acc.cookies if hasattr(acc, "cookies") else None
            p = Path(cookies_field) if cookies_field else None
            if p is None:
                out.append(CookieHealthItem(
                    platform="toutiao", account=acc.id,
                    healthy=False, detail="no cookies path configured",
                    last_check_at=now,
                ))
                continue
            ok, detail = _check_storage_state(p)
            out.append(CookieHealthItem(
                platform="toutiao", account=acc.id,
                healthy=ok, detail=detail, last_check_at=now,
            ))

    # 小红书
    plat = getattr(config.platforms, "xiaohongshu", None)
    if plat is not None and plat.accounts:
        ok, detail = _check_xhs_skills_path(config)
        for acc in plat.accounts:
            out.append(CookieHealthItem(
                platform="xiaohongshu", account=acc.id,
                healthy=ok, detail=detail, last_check_at=now,
            ))

    # X
    plat = getattr(config.platforms, "x", None)
    if plat is not None and plat.accounts:
        for acc in plat.accounts:
            creds = acc.credentials if hasattr(acc, "credentials") else None
            p = Path(creds) if creds else None
            if p is None:
                out.append(CookieHealthItem(
                    platform="x", account=acc.id,
                    healthy=False, detail="no credentials path configured",
                    last_check_at=now,
                ))
                continue
            ok, detail = _check_x_credentials(p)
            out.append(CookieHealthItem(
                platform="x", account=acc.id,
                healthy=ok, detail=detail, last_check_at=now,
            ))

    return out


__all__ = ["CookieHealthItem", "collect_cookie_health"]