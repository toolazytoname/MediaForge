"""M4-1 发布安全框架（HARD_PARTS §1 + TECH_SPEC §5.2/§9）。

`safe_publish` 是所有真实发布的唯一编排入口。封装三层防御：

  1. 配置层（第一道锁）：
     - publish.enabled = False → 整个 safe_publish 直接拒绝
     - publish.allowed_platforms 白名单外平台 → 拒绝
     - scheduled_at > now → 拒绝（未到排期）

  2. 乐观锁（第二道锁）：
     - `UPDATE publications SET status='publishing' WHERE id=? AND status='queued'`
     - rowcount==1 才继续；否则说明另一进程已抢走 / 状态已变

  3. UNIQUE 兜底（数据库层，第三道锁）：
     - publications 表 UNIQUE(content_id, platform, account_id)
     - 重复插入抛 IntegrityError（虽然 safe_publish 通常不会重复，但任何路径
       漏过都会在这里兜住）

  4. INTENT 日志（§1 决策 4）：
     - 调用 adapter.publish 前落一行 `INTENT publish p_xxx ...`
     - 进程死在发布后落库前 → 重启时 publishing 超时 30min → failed + 告警
       （人工核实平台是否已发出，绝不自动重试）

  5. timeout_publishings：
     - 定期清理 publishing 状态超过 timeout_minutes 的记录 → failed
     - cmd_publish 启动时调用一次（不引入额外 cron）

M4-1 不实现具体平台 publisher；M4-2（M4-3）填具体 adapter。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from pipeline import db
from pipeline.config import PublishConfig
from pipeline.models import (
    Content,
    Publication,
    PublicationStatus,
)
from pipeline.publishers.base import (
    AccountConfig,
    PostBundle,
    PublishError,
    PublishResult,
    PublisherAdapter,
)
from pipeline.utils.log import get_logger, log_event


INTENT_LOG_PREFIX = "INTENT publish"


# ── 数据类 ─────────────────────────────────────────────────


@dataclass(frozen=True)
class SafePublishResult:
    published: bool
    reason: str = ""
    platform_post_id: str | None = None
    url: str | None = None
    dry_run: bool = False


# ── 编排主入口 ────────────────────────────────────────────


def safe_publish(
    conn: sqlite3.Connection,
    publication: Publication,
    adapter: PublisherAdapter,
    *,
    config: PublishConfig,
    account: AccountConfig,
    dry_run: bool,
    now_iso: str,
    log_dir: Path | str = "logs",
) -> SafePublishResult:
    """单条 publication 安全发布编排。

    不写 DB 的逻辑：
      1. config.enabled = False → 返回（不调 adapter）
      2. adapter.platform 不在 allowed_platforms → 返回
      3. scheduled_at > now → 返回

    写 DB 的部分（事务化）：
      A. 乐观锁抢锁：UPDATE ... WHERE status='queued'（rowcount==1 才继续）
      B. INTENT 日志
      C. adapter.validate(bundle) — 本地格式校验
      D. adapter.publish(bundle, account, dry_run) — 真实发布
      E. 落库 status=published + platform_post_id + url
         或 status=failed + error
    """
    logger = get_logger("publish", log_dir=log_dir)

    # ── 第一道锁：配置层 ──
    if not config.enabled:
        return SafePublishResult(
            published=False, reason="publish is disabled",
        )
    if (config.allowed_platforms
            and adapter.platform not in config.allowed_platforms):
        return SafePublishResult(
            published=False,
            reason=f"platform {adapter.platform!r} not in allowed_platforms",
        )

    # ── scheduled_at 检查 ──
    if not _is_due(publication.scheduled_at, now_iso):
        return SafePublishResult(
            published=False,
            reason=f"not due yet (scheduled_at={publication.scheduled_at})",
        )

    # ── 状态预检（非锁，仅早返回） ──
    if publication.status != PublicationStatus.QUEUED.value:
        return SafePublishResult(
            published=False,
            reason=f"publication status is {publication.status!r}, "
                   "not queued (already processed?)",
        )

    # ── 构建 bundle（查 content 拿正文/标题等） ──
    content = db.get_content(conn, publication.content_id)
    if content is None:
        return SafePublishResult(
            published=False,
            reason=f"content not found: {publication.content_id}",
        )
    bundle = build_post_bundle(conn, publication, content=content)

    # ── 第二道锁：乐观锁抢锁 ──
    now = db.now_utc()
    cur = conn.execute(
        "UPDATE publications SET status=?, updated_at=? "
        "WHERE id=? AND status=?",
        (PublicationStatus.PUBLISHING.value, now,
         publication.id, PublicationStatus.QUEUED.value),
    )
    conn.commit()
    if cur.rowcount != 1:
        # 另一进程已抢走 / 状态已变（§1 决策 1）
        return SafePublishResult(
            published=False,
            reason="failed to acquire publishing lock (another worker?)",
        )

    # ── INTENT 日志（§1 决策 4） ──
    log_event(
        logger, 20,
        f"{INTENT_LOG_PREFIX} {publication.id} "
        f"platform={adapter.platform} account={account.id} "
        f"dry_run={dry_run}",
        stage="publish", ref_id=publication.id,
    )

    # ── 本地格式校验 ──
    issues = adapter.validate(bundle)
    if issues:
        _mark_failed(conn, publication.id, "validate failed: " + "; ".join(issues))
        return SafePublishResult(
            published=False, reason=f"validate: {'; '.join(issues)}",
        )

    # ── 真实发布 ──
    try:
        result = adapter.publish(bundle, account, dry_run=dry_run)
    except PublishError as e:
        _mark_failed(conn, publication.id, str(e))
        log_event(
            logger, 30,
            f"publish failed: {e}",
            stage="publish", ref_id=publication.id,
        )
        return SafePublishResult(
            published=False, reason=f"publish error: {e}",
        )
    except Exception as e:
        # 任何非 PublishError 也接住（不被外层吞）
        _mark_failed(conn, publication.id, f"unexpected: {e}")
        log_event(
            logger, 40,
            f"publish unexpected error: {e!r}",
            stage="publish", ref_id=publication.id,
        )
        return SafePublishResult(
            published=False, reason=f"unexpected error: {e}",
        )

    # ── 落库：published ──
    conn.execute(
        "UPDATE publications SET status=?, published_at=?, "
        "platform_post_id=?, platform_url=?, error=NULL, updated_at=? "
        "WHERE id=?",
        (PublicationStatus.PUBLISHED.value, now,
         result.platform_post_id, result.url, now,
         publication.id),
    )
    conn.commit()

    return SafePublishResult(
        published=True,
        platform_post_id=result.platform_post_id,
        url=result.url,
        dry_run=dry_run,
    )


# ── 超时清理 ───────────────────────────────────────────────


def timeout_publishings(
    conn: sqlite3.Connection,
    *,
    timeout_minutes: int = 30,
    now_iso: str,
) -> int:
    """清理 publishing 状态超过 timeout_minutes 的记录 → failed。

    返回清理条数。
    §1 决策 3：进程死在发布后落库前，重启时发现 publishing 超时 → failed
    + 告警（由调用方负责）。**绝不自动重试**（人工核实平台是否真发出）。
    """
    # now_iso - timeout_minutes (ISO 字符串减法不安全，转 datetime)
    now = _parse_iso(now_iso)
    cutoff = (
        now - timedelta(seconds=timeout_minutes * 60)
    ).isoformat()

    cur = conn.execute(
        "UPDATE publications SET status=?, error=?, updated_at=? "
        "WHERE status=? AND updated_at<?",
        (PublicationStatus.FAILED.value,
         f"publishing timeout > {timeout_minutes}min (manual check needed)",
         db.now_utc(),
         PublicationStatus.PUBLISHING.value,
         cutoff),
    )
    conn.commit()
    return cur.rowcount


# ── helpers ────────────────────────────────────────────────


def build_post_bundle(
    conn: sqlite3.Connection,
    publication: Publication,
    *,
    content: Content | None = None,
) -> PostBundle:
    """从 publication + content 构造 PostBundle（给 adapter 用）。

    content 可注入（避免重复查询），否则按 publication.content_id 取。
    派生格式文件（如 toutiao.md / xiaohongshu/caption.md）的存在性
    留给 adapter.validate() 检查；bundle 本身只负责"该有什么"。
    """
    from pipeline.utils.errors import CreateError
    if content is None:
        content = db.get_content(conn, publication.content_id)
    if content is None:
        raise CreateError(
            f"content not found for publication {publication.id}: "
            f"content_id={publication.content_id}",
        )
    # body_path：基于 canonical.md 兜底（adapter 自己根据 platform 选派生文件）
    body_path = Path(content.canonical_path)
    return PostBundle(
        content_id=content.id,
        title=content.title,
        body_path=body_path,
        media_paths=(),  # M4-2+ 由各 adapter 自己取派生媒体
        tags=(),         # 同上
        extra={
            "platform": publication.platform,
            "publication_id": publication.id,
        },
    )


def _is_due(scheduled_at: str, now_iso: str) -> bool:
    """scheduled_at <= now → 可发。"""
    try:
        sched = _parse_iso(scheduled_at)
        now = _parse_iso(now_iso)
    except (ValueError, TypeError):
        # 解析失败保守：放过（让上层去判断）
        return True
    return sched <= now


def _parse_iso(s: str) -> datetime:
    """ISO8601 → tz-aware datetime（UTC 兜底）。"""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _mark_failed(conn: sqlite3.Connection, pub_id: str, error: str) -> None:
    """把 publication 标 failed + 写入 error 字段。"""
    conn.execute(
        "UPDATE publications SET status=?, error=?, updated_at=? "
        "WHERE id=?",
        (PublicationStatus.FAILED.value, error[:1000],  # 截断防爆
         db.now_utc(), pub_id),
    )
    conn.commit()


# ── 需要 import 的 timedelta ───────────────────────────────
from datetime import timedelta  # noqa: E402  (需要 _is_due / timeout 中用到)


__all__ = [
    "SafePublishResult",
    "safe_publish",
    "timeout_publishings",
    "build_post_bundle",
    "INTENT_LOG_PREFIX",
]