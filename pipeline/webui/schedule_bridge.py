"""M10-11 阶段 D：手动排期桥层（webui → db.insert_publication）。

M10 P2「图文全流程」第四步——对 approved (or gated) 内容手动造一条
queued publication。前三步（创建/衍生/写端点）已完成。

设计要点（与 creation_bridge / derivative_bridge / write_action_bridge 同构）：
  - 不重写 db.insert_publication（薄封装）
  - 6 个 Error class → API 层映射到具体 HTTP 码
  - now 参数显式注入（单测可控），缺省 = db.now_utc()
  - cfg 校验用 platform / account 真校验（不空 mock）
  - scheduled_at ISO8601 解析 + 「必须未来」两步校验（防脏数据）
  - UNIQUE 冲突由 db 层抛 sqlite3.IntegrityError → 包为 DuplicateScheduleError

错误映射（HARD_PARTS §1 防重复发布是全系统最高优先级）：
  * ContentNotFoundError → 404
  * ContentWrongStatusError → 400 (code=wrong_status)
  * PlatformNotConfiguredError → 400 (code=platform_not_configured)
  * AccountNotFoundError → 400 (code=account_not_found)
  * InvalidScheduledAtError → 400 (code=invalid_scheduled_at)
  * DuplicateScheduleError → 409 (code=duplicate_schedule)
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from pipeline import db
from pipeline.models import (
    Content,
    ContentStatus,
    Publication,
    PublicationStatus,
)
from pipeline.utils.ids import new_id


# ── Exceptions（API 层映射：404 / 400 / 409）────────────────


class ContentNotFoundError(ValueError):
    """content_id 不存在 → 404。前端按 error code 区分。"""


class ContentWrongStatusError(ValueError):
    """content 状态不在 {approved, gated} 白名单内 → 400。"""


class PlatformNotConfiguredError(ValueError):
    """platform 字段值不在 cfg.platforms 已配置集合 → 400。"""


class AccountNotFoundError(ValueError):
    """account_id 不在该 platform 的 accounts 列表内 → 400。"""


class InvalidScheduledAtError(ValueError):
    """scheduled_at 解析失败或已过去 → 400。"""


class DuplicateScheduleError(ValueError):
    """(content_id, platform, account_id) 已存在 queued/cancelled 等记录 → 409。

    命中 db.UNIQUE(content_id, platform, account_id) 兜底——防重复发布最后防线
    （HARD_PARTS §1）。这是 db 层直接抛 sqlite3.IntegrityError，本 bridge
    捕获后翻译成专属 Error，避免前端看到原始异常名。
    """


# ── 状态白名单 ──────────────────────────────────────────────

# 允许手动排期的内容状态集合：
#   - approved     人审通过，准备发布
#   - gated        已过门禁待审（允许运营「先排上、人审若打回可手动 cancel」；
#                  规格说明手册允许 approved 之外再次手动覆盖排期，gated 是同
#                  性质「终态前最后可排期点」）
# 显式排除：
#   - draft / rejected_by_human / discarded / failed / done
#     终态或未过门禁，禁止触发排期
_ALLOWED_STATUSES: frozenset[str] = frozenset({
    ContentStatus.APPROVED.value,
    ContentStatus.GATED.value,
})


# ── helpers ─────────────────────────────────────────────────


def _parse_iso(scheduled_at: str) -> datetime:
    """解析 ISO8601 → aware datetime (UTC)。

    Raises:
        InvalidScheduledAtError: 解析失败。
    """
    if not isinstance(scheduled_at, str) or not scheduled_at.strip():
        raise InvalidScheduledAtError(
            f"scheduled_at must be non-empty string, got {scheduled_at!r}"
        )
    raw = scheduled_at.strip()
    # 兼容 Z 后缀（与 write_action_bridge._parse_iso_utc 同）
    try:
        normalized = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
    except (ValueError, TypeError) as e:
        raise InvalidScheduledAtError(
            f"scheduled_at must be ISO8601, got {scheduled_at!r}: {e}"
        ) from e
    # naive → 视为 UTC（与库内其它字段约定一致）
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _format_iso_utc(dt: datetime) -> str:
    """aware datetime → ISO8601 字符串（带时区）。"""
    return dt.isoformat()


def _get_platform_cfg(cfg_obj: Any, platform: str) -> Any | None:
    """从 cfg.platforms 取 platform 配置。缺/None → None。

    PlatformsConfig 在 platforms: 下声明了 x/toutiao/xiaohongshu/douyin
    四个 known 字段，外加 extra=forbid（未声明 platform 字段访问 → None）。
    """
    if cfg_obj is None:
        return None
    platforms = getattr(cfg_obj, "platforms", None)
    if platforms is None:
        return None
    return getattr(platforms, platform, None)


def _account_ids_of(platform_cfg: Any) -> list[str]:
    """列出 platform_cfg.accounts 内每个账号的 id。

    兼容 AccountAPI / AccountPlaywright 两种 schema（M5 等扩展可能加
    第三个 Account* 类型；这里用 list comprehension + hasattr 兜底）。
    """
    if platform_cfg is None:
        return []
    accounts = getattr(platform_cfg, "accounts", None) or []
    ids: list[str] = []
    for a in accounts:
        if a is None:
            continue
        aid = getattr(a, "id", None)
        if isinstance(aid, str):
            ids.append(aid)
    return ids


# ── 主入口 ─────────────────────────────────────────────────


def schedule_for_content(
    conn: sqlite3.Connection,
    content_id: str,
    platform: str,
    account_id: str,
    scheduled_at: str,
    *,
    cfg_obj: Any | None = None,
    now: str | None = None,
) -> Publication:
    """为一条 approved (or gated) content 手工造一条 queued publication。

    复用 db.insert_publication（不走 SQL 助手外其它裸路径）；
    校验链：content 存在 → status 白名单 → platform 在 cfg → account 在
    platform.accounts → scheduled_at ISO 解析 → scheduled_at ≥ now →
    UNIQUE(content_id,platform,account_id) 兜底。

    Args:
        conn: SQLite 连接（由调用方管理生命周期）。
        content_id: 内容 id（'c_' 前缀）。
        platform: 平台名（'x' / 'toutiao' / 'xiaohongshu' / 'douyin'）。
        account_id: cfg.platforms.<plat>.accounts[].id 别名。
        scheduled_at: ISO8601 时间字符串（带或不带时区均可）。
        cfg_obj: 已加载的 AppConfig（None = 不校验 platform/account，
            仅做其它校验——测试可注入；生产应由调用方从 deps.get_config() 取）。
        now: ISO8601 UTC 字符串。缺省 = db.now_utc()。测试可注入。

    Returns:
        已落库的 Publication（status=queued, retry_count=0）。

    Raises:
        ContentNotFoundError: content_id 不存在。
        ContentWrongStatusError: content 状态不在 {approved, gated}。
        PlatformNotConfiguredError: cfg_obj 非 None 且 platform 未配置。
        AccountNotFoundError: cfg_obj 非 None 且 account_id 不在该
            platform 的 accounts 列表内。
        InvalidScheduledAtError: scheduled_at 解析失败或已过去。
        DuplicateScheduleError: UNIQUE(content_id, platform, account_id) 命中。
    """
    if now is None:
        now = db.now_utc()

    # 1. content 存在性 + 状态白名单
    c: Content | None = db.get_content(conn, content_id)
    if c is None:
        raise ContentNotFoundError(f"content {content_id} not found")
    if c.status not in _ALLOWED_STATUSES:
        raise ContentWrongStatusError(
            f"content {content_id} status={c.status!r} not in allowed "
            f"{sorted(_ALLOWED_STATUSES)}"
        )

    # 2. platform 必须配置（cfg_obj 非 None 时校验）
    if cfg_obj is not None:
        plat_cfg = _get_platform_cfg(cfg_obj, platform)
        if plat_cfg is None:
            raise PlatformNotConfiguredError(
                f"platform {platform!r} is not configured in cfg.platforms"
            )
        # 3. account_id 必须在 platform_cfg.accounts 里
        if account_id not in _account_ids_of(plat_cfg):
            raise AccountNotFoundError(
                f"account_id {account_id!r} not found in "
                f"cfg.platforms.{platform}.accounts"
            )

    # 4. scheduled_at 解析 + 必须未来
    dt = _parse_iso(scheduled_at)
    now_dt = _parse_iso(now)  # now 必须是合法 ISO（db.now_utc() 保证）
    if dt < now_dt:
        raise InvalidScheduledAtError(
            f"scheduled_at {dt.isoformat()} is in the past "
            f"(now={now_dt.isoformat()})"
        )
    scheduled_at_iso = _format_iso_utc(dt)

    # 5. 构造 + 插入（UNIQUE 冲突由 db 层抛 sqlite3.IntegrityError）
    pub = Publication(
        id=new_id("p"),
        content_id=content_id,
        platform=platform,
        account_id=account_id,
        scheduled_at=scheduled_at_iso,
        published_at=None,
        platform_post_id=None,
        platform_url=None,
        error=None,
        retry_count=0,
        status=PublicationStatus.QUEUED.value,
        created_at=now,
        updated_at=now,
    )
    try:
        db.insert_publication(conn, pub)
    except Exception as e:
        # sqlite3.IntegrityError（UNIQUE 命中）
        msg = str(e).lower()
        if "unique" in msg or "constraint" in msg:
            raise DuplicateScheduleError(
                f"publication already exists for "
                f"(content_id={content_id}, platform={platform}, "
                f"account_id={account_id})"
            ) from e
        raise

    return pub


__all__ = [
    "schedule_for_content",
    "ContentNotFoundError",
    "ContentWrongStatusError",
    "PlatformNotConfiguredError",
    "AccountNotFoundError",
    "InvalidScheduledAtError",
    "DuplicateScheduleError",
]
