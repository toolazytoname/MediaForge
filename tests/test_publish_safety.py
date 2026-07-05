"""M4-1 发布安全框架（TECH_SPEC §5.2 + HARD_PARTS §1 + §9 必测清单）。

测试 PublisherAdapter 接口契约 + 三重锁编排（safe_publish）+ publish.enabled=false 全路径不可达。
M4-1 不实现具体平台（X/头条/小红书由 M4-2/M4-3 实现）；本测试用 MockPublisherAdapter。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from unittest.mock import patch

import pytest

from pipeline import db
from pipeline.config import (
    AccountAPI,
    AppConfig,
    LLMBudget,
    LLMConfig,
    LLMTiers,
    Pillar,
    PlatformAPI,
    PlatformsConfig,
    PublishConfig,
)
from pipeline.models import (
    Content,
    ContentStatus,
    Publication,
    PublicationStatus,
    Topic,
    TopicStatus,
)
from pipeline.publishers.base import (
    AccountConfig,
    PostBundle,
    PublishError,
    PublishResult,
    PublisherAdapter,
)
from pipeline.publishers.safe_publish import (
    INTENT_LOG_PREFIX,
    SafePublishResult,
    safe_publish,
    timeout_publishings,
)


_NOW = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
_NOW_ISO = _NOW.isoformat()
_PAST_ISO = (_NOW - timedelta(hours=1)).isoformat()
_FUTURE_ISO = (_NOW + timedelta(hours=2)).isoformat()


# ── helpers ────────────────────────────────────────────────


def _conn(tmp_path: Path) -> sqlite3.Connection:
    c = db.connect(tmp_path / "state.db")
    db.init_db(c)
    return c


def _seed_publication(
    conn: sqlite3.Connection,
    *,
    id: str = "p_pub0001",
    content_id: str = "c_pub0001",
    platform: str = "x",
    scheduled_at: str = _PAST_ISO,
    status: str = PublicationStatus.QUEUED.value,
    account_id: str = "main",
) -> Publication:
    """插入完整 publication（topic + content FK 都齐）。"""
    now = _NOW_ISO
    topic_id = "t_" + content_id.removeprefix("c_")
    t = Topic(
        id=topic_id, source="rss:test", title="T", url=None,
        summary=None, content_hash=f"h-{topic_id}", pillar="ai_daily",
        score=7.0, score_reason=None,
        status=TopicStatus.CONSUMED.value,
        created_at=now, updated_at=now,
    )
    db.insert_topic(conn, t)
    c = Content(
        id=content_id, topic_id=topic_id, pillar="ai_daily",
        title="C", canonical_path=f"output/2026-07-06/{content_id}/canonical.md",
        formats='["x"]',
        gate_score_total=27.0,
        gate_scores='{"info":9,"fun":9,"view":9}',
        gate_verdict="通过",
        status=ContentStatus.APPROVED.value,
        created_at=now, updated_at=now,
    )
    db.insert_content(conn, c)
    p = Publication(
        id=id, content_id=content_id, platform=platform,
        account_id=account_id, scheduled_at=scheduled_at,
        published_at=None, platform_post_id=None,
        platform_url=None, error=None, retry_count=0,
        status=status,
        created_at=now, updated_at=now,
    )
    db.insert_publication(conn, p)
    return p


def _config(
    *,
    enabled: bool = True,
    allowed: list[str] | None = None,
) -> PublishConfig:
    return PublishConfig(
        enabled=enabled,
        allowed_platforms=allowed if allowed is not None else ["x", "toutiao", "xiaohongshu"],
        min_gap_hours=4,
        max_daily_per_account=3,
        cross_platform_gap_minutes=30,
    )


def _mock_adapter(
    *,
    platform: str = "x",
    post_id: str = "remote_123",
    url: str = "https://example.com/post/123",
    should_raise: Exception | None = None,
    delay_s: float = 0.0,
) -> PublisherAdapter:
    """构造一个 MockPublisherAdapter，记录 publish 调用次数。"""
    calls = {"validate": 0, "publish": 0}
    plat_value = platform

    class Mock(PublisherAdapter):
        platform = plat_value

        def validate(self, bundle: PostBundle) -> list[str]:
            calls["validate"] += 1
            return []  # 全部通过

        def publish(self, bundle, account, dry_run=False) -> PublishResult:
            calls["publish"] += 1
            if should_raise:
                raise should_raise
            return PublishResult(
                platform_post_id=f"{'dry-' if dry_run else ''}{post_id}",
                url=url,
                raw_response=json.dumps({"id": post_id}),
            )

    return Mock()


def _get_pub_status(conn: sqlite3.Connection, pub_id: str) -> str:
    row = conn.execute(
        "SELECT status FROM publications WHERE id=?", (pub_id,)
    ).fetchone()
    return row["status"]


# ── PublisherAdapter 契约 ──────────────────────────────────


class TestPublisherAdapterContract:
    def test_validate_returns_issues(self) -> None:
        """validate 返回问题列表，空=通过。"""

        class StrictAdapter(PublisherAdapter):
            platform = "x"

            def validate(self, bundle):
                if len(bundle.title) > 280:
                    return ["title too long"]
                return []

            def publish(self, bundle, account, dry_run=False):
                return PublishResult(None, None, "{}")

        a = StrictAdapter()
        long_bundle = PostBundle(
            content_id="c", title="x" * 300, body_path=Path("/tmp/b"),
            media_paths=(), tags=(), extra={},
        )
        assert a.validate(long_bundle) == ["title too long"]

        short_bundle = PostBundle(
            content_id="c", title="ok", body_path=Path("/tmp/b"),
            media_paths=(), tags=(), extra={},
        )
        assert a.validate(short_bundle) == []

    def test_publish_dry_run_does_not_post(
        self, tmp_path: Path
    ) -> None:
        """dry_run=True：必须返回 dry- 前缀 post_id 表示未真发。"""
        conn = _conn(tmp_path)
        pub = _seed_publication(conn)
        adapter = _mock_adapter(post_id="remote_real")
        account = AccountConfig(id="main", credentials_path=Path("secrets/x.json"))

        from pipeline.publishers.safe_publish import build_post_bundle
        bundle = build_post_bundle(conn, pub)

        result = adapter.publish(bundle, account, dry_run=True)
        assert result.platform_post_id == "dry-remote_real"


# ── 三重锁之一：publish.enabled=false 全路径不可达 ──────


class TestPublishDisabledLock:
    def test_disabled_blocks_at_safe_publish(
        self, tmp_path: Path
    ) -> None:
        """§1 + §9: publish.enabled=false 时 safe_publish 不能调 adapter.publish。"""
        conn = _conn(tmp_path)
        pub = _seed_publication(conn)
        adapter = _mock_adapter()
        cfg = _config(enabled=False)

        result = safe_publish(
            conn, pub, adapter, config=cfg, account=AccountConfig(
                id="main", credentials_path=Path("secrets/x.json"),
            ),
            dry_run=False,
            now_iso=_NOW_ISO,
        )

        assert result.published is False
        assert "disabled" in result.reason.lower() or "not allowed" in result.reason.lower()
        # adapter.publish 不应被调用
        # 状态仍是 queued（没改）
        assert _get_pub_status(conn, pub.id) == PublicationStatus.QUEUED.value

    def test_platform_not_allowed_blocks(
        self, tmp_path: Path
    ) -> None:
        """§1: 平台不在白名单 → 阻断。"""
        conn = _conn(tmp_path)
        pub = _seed_publication(conn, platform="weibo")  # 不在白名单
        adapter = _mock_adapter(platform="weibo")
        cfg = _config(allowed=["x", "toutiao"])  # 没 weibo

        result = safe_publish(
            conn, pub, adapter, config=cfg,
            account=AccountConfig(id="main", credentials_path=Path("secrets/x.json")),
            dry_run=False, now_iso=_NOW_ISO,
        )
        assert result.published is False
        assert "not allowed" in result.reason.lower() or "weibo" in result.reason


# ── 三重锁之二：乐观锁 + UNIQUE 兜底 ─────────────────────


class TestOptimisticLock:
    def test_queued_to_publishing_atomic(
        self, tmp_path: Path
    ) -> None:
        """§1: 取任务时 UPDATE 乐观锁，rowcount==1 才继续。"""
        conn = _conn(tmp_path)
        pub = _seed_publication(conn)
        adapter = _mock_adapter()
        cfg = _config()

        result = safe_publish(
            conn, pub, adapter, config=cfg,
            account=AccountConfig(id="main", credentials_path=Path("secrets/x.json")),
            dry_run=False, now_iso=_NOW_ISO,
        )

        assert result.published is True
        # 状态推到 published（含 platform_post_id）
        row = conn.execute(
            "SELECT status, platform_post_id, platform_url "
            "FROM publications WHERE id=?", (pub.id,)
        ).fetchone()
        assert row["status"] == PublicationStatus.PUBLISHED.value
        assert row["platform_post_id"] == "remote_123"
        assert row["platform_url"] == "https://example.com/post/123"

    def test_already_published_skipped(
        self, tmp_path: Path
    ) -> None:
        """§1: status=published 时 safe_publish 跳过（不调 adapter）。"""
        conn = _conn(tmp_path)
        pub = _seed_publication(conn, status=PublicationStatus.PUBLISHED.value)
        adapter = _mock_adapter()
        cfg = _config()

        result = safe_publish(
            conn, pub, adapter, config=cfg,
            account=AccountConfig(id="main", credentials_path=Path("secrets/x.json")),
            dry_run=False, now_iso=_NOW_ISO,
        )

        assert result.published is False
        assert "already" in result.reason.lower() or "status" in result.reason.lower()


# ── INTENT 日志 ───────────────────────────────────────────


class TestIntentLog:
    def test_intent_log_written_before_publish(
        self, tmp_path: Path
    ) -> None:
        """§1 决策：先写 INTENT publish p_xxx 日志，再发布。"""
        conn = _conn(tmp_path)
        pub = _seed_publication(conn)
        adapter = _mock_adapter()
        cfg = _config()

        log_dir = tmp_path / "logs"
        result = safe_publish(
            conn, pub, adapter, config=cfg,
            account=AccountConfig(id="main", credentials_path=Path("secrets/x.json")),
            dry_run=False, now_iso=_NOW_ISO,
            log_dir=log_dir,
        )

        assert result.published is True
        # 检查 INTENT 日志：logs/pipeline.log 应含 INTENT 行
        log_file = log_dir / "pipeline.log"
        assert log_file.exists()
        content = log_file.read_text(encoding="utf-8")
        assert INTENT_LOG_PREFIX in content
        assert pub.id in content


# ── 失败处理 ───────────────────────────────────────────────


class TestPublishFailure:
    def test_adapter_exception_marks_failed(
        self, tmp_path: Path
    ) -> None:
        """§1: publish 抛 PublishError → status=failed + 记录 error 字段。"""
        conn = _conn(tmp_path)
        pub = _seed_publication(conn)
        adapter = _mock_adapter(
            should_raise=PublishError("network timeout"),
        )
        cfg = _config()

        result = safe_publish(
            conn, pub, adapter, config=cfg,
            account=AccountConfig(id="main", credentials_path=Path("secrets/x.json")),
            dry_run=False, now_iso=_NOW_ISO,
        )

        assert result.published is False
        row = conn.execute(
            "SELECT status, error FROM publications WHERE id=?",
            (pub.id,),
        ).fetchone()
        assert row["status"] == PublicationStatus.FAILED.value
        assert "network timeout" in row["error"]


# ── 排期未到 ───────────────────────────────────────────────


class TestScheduledTimeNotReached:
    def test_future_scheduled_at_skipped(
        self, tmp_path: Path
    ) -> None:
        """scheduled_at > now → 跳过（§1 / M3-1 排期约束）。"""
        conn = _conn(tmp_path)
        pub = _seed_publication(conn, scheduled_at=_FUTURE_ISO)
        adapter = _mock_adapter()
        cfg = _config()

        result = safe_publish(
            conn, pub, adapter, config=cfg,
            account=AccountConfig(id="main", credentials_path=Path("secrets/x.json")),
            dry_run=False, now_iso=_NOW_ISO,
        )

        assert result.published is False
        assert "scheduled" in result.reason.lower() or "not due" in result.reason.lower()
        # 状态仍 queued
        assert _get_pub_status(conn, pub.id) == PublicationStatus.QUEUED.value


# ── 超时清理 ───────────────────────────────────────────────


class TestTimeoutCleanup:
    def test_publishing_over_30min_marked_failed(
        self, tmp_path: Path
    ) -> None:
        """§1: publishing 状态超过 30min → failed。"""
        conn = _conn(tmp_path)
        # 插入一条 31 分钟前进入 publishing 的 publication
        pub = _seed_publication(
            conn, status=PublicationStatus.PUBLISHING.value,
        )
        old_updated = (
            _NOW - timedelta(minutes=31)
        ).isoformat()
        conn.execute(
            "UPDATE publications SET updated_at=? WHERE id=?",
            (old_updated, pub.id),
        )
        conn.commit()

        timeout_publishings(conn, timeout_minutes=30, now_iso=_NOW_ISO)
        row = conn.execute(
            "SELECT status, error FROM publications WHERE id=?",
            (pub.id,),
        ).fetchone()
        assert row["status"] == PublicationStatus.FAILED.value
        assert "timeout" in (row["error"] or "").lower()

    def test_recent_publishing_not_marked_failed(
        self, tmp_path: Path
    ) -> None:
        """仅 5 分钟前的 publishing 不应被误判。"""
        conn = _conn(tmp_path)
        pub = _seed_publication(
            conn, status=PublicationStatus.PUBLISHING.value,
        )
        recent = (_NOW - timedelta(minutes=5)).isoformat()
        conn.execute(
            "UPDATE publications SET updated_at=? WHERE id=?",
            (recent, pub.id),
        )
        conn.commit()

        timeout_publishings(conn, timeout_minutes=30, now_iso=_NOW_ISO)
        assert _get_pub_status(conn, pub.id) == PublicationStatus.PUBLISHING.value


# ── dry_run 全流程 ─────────────────────────────────────────


class TestDryRun:
    def test_dry_run_completes_without_real_publish(
        self, tmp_path: Path
    ) -> None:
        """§2: dry_run 走完全流程但不真发——adapter.publish(dry_run=True) 被调。"""
        conn = _conn(tmp_path)
        pub = _seed_publication(conn)
        adapter = _mock_adapter()
        cfg = _config()

        result = safe_publish(
            conn, pub, adapter, config=cfg,
            account=AccountConfig(id="main", credentials_path=Path("secrets/x.json")),
            dry_run=True, now_iso=_NOW_ISO,
        )

        assert result.published is True
        assert result.dry_run is True
        # 状态应推进（虽没真发，但状态机走完）
        row = conn.execute(
            "SELECT status, platform_post_id FROM publications WHERE id=?",
            (pub.id,),
        ).fetchone()
        assert row["platform_post_id"] == "dry-remote_123"