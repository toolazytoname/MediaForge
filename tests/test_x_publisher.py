"""M4-2 X Publisher 测试（TDD — RED 阶段）。

测试契约：
- pipeline/publishers/x_api.py::XApiPublisher 实现 PublisherAdapter 接口
- X API v2 free tier（POST https://api.twitter.com/2/tweets）
- OAuth2 bearer token 走 secrets/x_<account>.json（与 config 脱敏路径严格分离）
- thread 链式发布：第 i 条 in_reply_to_tweet_id = 第 i-1 条返回的 id
- 中途失败 → publication.error 记录已发部分（"partial: id1,id2"）
- dry_run 走完全流程但不调 POST
- LoginExpired 不重发（同重复帖防护联动 SAFE_PUBLISH）

X thread 来源：output/<date>/<c_id>/x/thread.md，每条格式 "1/N tweet..."
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pipeline import db
from pipeline.config import (
    AccountAPI,
    AppConfig,
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


# ── 公共 fixture ──────────────────────────────────────────

_NOW = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
_NOW_ISO = _NOW.isoformat()
_PAST_ISO = (_NOW - timedelta(hours=1)).isoformat()


def _conn(tmp_path: Path) -> sqlite3.Connection:
    c = db.connect(tmp_path / "state.db")
    db.init_db(c)
    return c


def _seed_publication(
    conn: sqlite3.Connection,
    *,
    content_id: str = "c_xpub001",
    out_root: Path,
) -> tuple[Publication, Path]:
    """插入完整 publication + 写一个 x/thread.md 文件（含 5 条）。

    `out_root` 由 caller 注入；这里直接基于它建 content_dir。
    早期版本依赖 PRAGMA database_list 反推，但 tmp_path db 连接可能就是
    `:memory:`，不可靠。让 caller 传 out_root 更显式。
    """
    now = _NOW_ISO
    topic_id = "t_" + content_id.removeprefix("c_")
    topic = Topic(
        id=topic_id, source="rss:test", title="T", url=None,
        summary=None, content_hash=f"h-{topic_id}", pillar="ai_daily",
        score=7.0, score_reason=None,
        status=TopicStatus.CONSUMED.value,
        created_at=now, updated_at=now,
    )
    db.insert_topic(conn, topic)

    content_dir = out_root / content_id
    content_dir.mkdir(parents=True, exist_ok=True)
    thread_path = content_dir / "x" / "thread.md"
    thread_path.parent.mkdir(parents=True, exist_ok=True)
    thread_path.write_text(
        "\n\n".join(f"{i+1}/5 Thread tweet #{i+1} about AI." for i in range(5)),
        encoding="utf-8",
    )

    content = Content(
        id=content_id, topic_id=topic_id, pillar="ai_daily",
        title="X test",
        canonical_path=str(content_dir / "canonical.md"),
        formats='["x"]',
        gate_score_total=27.0,
        gate_scores='{"info":9,"fun":9,"view":9}',
        gate_verdict="通过",
        status=ContentStatus.APPROVED.value,
        created_at=now, updated_at=now,
    )
    db.insert_content(conn, content)

    pub = Publication(
        id="p_xpub001", content_id=content_id, platform="x",
        account_id="main", scheduled_at=_PAST_ISO,
        published_at=None, platform_post_id=None,
        platform_url=None, error=None, retry_count=0,
        status=PublicationStatus.QUEUED.value,
        created_at=now, updated_at=now,
    )
    db.insert_publication(conn, pub)
    return pub, thread_path


# ── split_thread 单元测试 ─────────────────────────────────


class TestSplitThread:
    """thread.md → list[tweet]，每条 ≤ 260 字符。"""

    def test_split_basic_numbered(self) -> None:
        from pipeline.publishers.x_api import split_thread
        body = "1/3 first tweet\n\n2/3 second tweet here\n\n3/3 last tweet ever"
        tweets = split_thread(body)
        assert tweets == ["first tweet", "second tweet here", "last tweet ever"]

    def test_split_strips_whitespace(self) -> None:
        from pipeline.publishers.x_api import split_thread
        body = "  1/2   tweet one  \n\n  2/2   tweet two  "
        tweets = split_thread(body)
        assert tweets == ["tweet one", "tweet two"]

    def test_split_preserves_links_and_emoji(self) -> None:
        from pipeline.publishers.x_api import split_thread
        body = "1/1 看看 https://example.com/foo 👀"
        tweets = split_thread(body)
        assert tweets == ["看看 https://example.com/foo 👀"]

    def test_split_ignores_non_numbered_lines(self) -> None:
        from pipeline.publishers.x_api import split_thread
        body = (
            "# Title heading\n\n"
            "1/2 only first\n\n"
            "some extra commentary\n\n"
            "2/2 only second"
        )
        tweets = split_thread(body)
        assert tweets == ["only first", "only second"]


# ── 凭据加载 ──────────────────────────────────────────────


class TestCredentialLoad:
    def test_load_bearer_token(self, tmp_path: Path) -> None:
        """secrets/x_<account>.json 含 {"bearer_token": "..."} 字段。"""
        creds = tmp_path / "x_main.json"
        creds.write_text(json.dumps({"bearer_token": "AAAAtesttoken"}))

        from pipeline.publishers.x_api import load_x_credentials
        token = load_x_credentials(creds)
        assert token == "AAAAtesttoken"

    def test_load_missing_file_raises(self, tmp_path: Path) -> None:
        from pipeline.publishers.x_api import load_x_credentials
        with pytest.raises(FileNotFoundError):
            load_x_credentials(tmp_path / "nonexistent.json")

    def test_load_missing_field_raises(self, tmp_path: Path) -> None:
        creds = tmp_path / "x_main.json"
        creds.write_text(json.dumps({"api_key": "wrong_field"}))
        from pipeline.publishers.x_api import load_x_credentials
        with pytest.raises(ValueError, match="bearer_token"):
            load_x_credentials(creds)


# ── validate（不触网络） ──────────────────────────────────


class TestValidate:
    def _make_adapter(self) -> PublisherAdapter:
        from pipeline.publishers.x_api import XApiPublisher
        return XApiPublisher(bearer_token="dummy", http_post=lambda *a, **k: None)

    def _bundle(self, thread_path: Path) -> PostBundle:
        return PostBundle(
            content_id="c_x", title="t",
            body_path=thread_path, media_paths=(), tags=(), extra={},
        )

    def test_validate_thread_missing_file(self, tmp_path: Path) -> None:
        a = self._make_adapter()
        bundle = self._bundle(tmp_path / "nope.md")
        issues = a.validate(bundle)
        assert any("not found" in i.lower() or "missing" in i.lower() for i in issues)

    def test_validate_thread_too_long(self, tmp_path: Path) -> None:
        long_thread = tmp_path / "thread.md"
        long_thread.write_text("1/1 " + ("x" * 400), encoding="utf-8")
        a = self._make_adapter()
        bundle = self._bundle(long_thread)
        issues = a.validate(bundle)
        assert any("260" in i or "too long" in i.lower() for i in issues)

    def test_validate_thread_too_many(self, tmp_path: Path) -> None:
        # 11 条（合法 3-10 条）
        thread_path = tmp_path / "thread.md"
        thread_path.write_text(
            "\n\n".join(f"{i+1}/11 tweet {i+1}" for i in range(11)),
            encoding="utf-8",
        )
        a = self._make_adapter()
        bundle = self._bundle(thread_path)
        issues = a.validate(bundle)
        assert any("3" in i and "10" in i for i in issues)

    def test_validate_thread_empty(self, tmp_path: Path) -> None:
        thread_path = tmp_path / "thread.md"
        thread_path.write_text("", encoding="utf-8")
        a = self._make_adapter()
        bundle = self._bundle(thread_path)
        issues = a.validate(bundle)
        assert any("empty" in i.lower() for i in issues)

    def test_validate_happy_path(self, tmp_path: Path) -> None:
        thread_path = tmp_path / "thread.md"
        thread_path.write_text(
            "\n\n".join(f"{i+1}/5 tweet {i+1}" for i in range(5)),
            encoding="utf-8",
        )
        a = self._make_adapter()
        bundle = self._bundle(thread_path)
        assert a.validate(bundle) == []


# ── publish（mock httpx） ────────────────────────────────


class TestPublishThread:
    """调 X API v2 链式发帖，失败路径正确。"""

    def _make_adapter(
        self,
        responses: list[dict],
        bundle: PostBundle,
    ) -> tuple[PublisherAdapter, list[dict]]:
        """构造 XApiPublisher + 模拟 httpx 调用。返回 adapter 与 captured_calls。"""
        from pipeline.publishers.x_api import XApiPublisher
        captured: list[dict] = []

        def fake_post(url: str, *, headers: dict, body: dict, timeout: float) -> dict:
            captured.append({"url": url, "body": body, "headers": headers})
            idx = len(captured) - 1
            if idx >= len(responses):
                raise PublishError("ran out of mock responses")
            resp = responses[idx]
            if "error" in resp:
                raise PublishError(f"X API error: {resp['error']}")
            return resp

        a = XApiPublisher(bearer_token="dummy", http_post=fake_post)
        return a, captured

    def _account(self) -> AccountConfig:
        return AccountConfig(
            id="main",
            credentials_path=Path("secrets/x_main.json"),
        )

    def test_thread_posted_in_chain(
        self, tmp_path: Path,
    ) -> None:
        conn = _conn(tmp_path)
        pub, thread_path = _seed_publication(conn, out_root=tmp_path)
        bundle = PostBundle(
            content_id=pub.content_id, title="t",
            body_path=thread_path, media_paths=(), tags=(), extra={},
        )

        responses = [
            {"data": {"id": "111", "text": "t1"}},
            {"data": {"id": "222", "text": "t2"}},
            {"data": {"id": "333", "text": "t3"}},
            {"data": {"id": "444", "text": "t4"}},
            {"data": {"id": "555", "text": "t5"}},
        ]
        adapter, calls = self._make_adapter(responses, bundle)
        result = adapter.publish(bundle, self._account(), dry_run=False)

        # 5 次 POST
        assert len(calls) == 5
        # 第 1 条无 reply_to；后面 4 条 reply 到上一条 id
        assert "in_reply_to_tweet_id" not in calls[0]["body"]
        assert calls[1]["body"]["in_reply_to_tweet_id"] == "111"
        assert calls[2]["body"]["in_reply_to_tweet_id"] == "222"
        assert calls[3]["body"]["in_reply_to_tweet_id"] == "333"
        assert calls[4]["body"]["in_reply_to_tweet_id"] == "444"
        # header 含 bearer
        assert calls[0]["headers"]["Authorization"] == "Bearer dummy"
        # 最终 result：第 5 条 id + url 拼 https://x.com/<user>/status/<id>（这里我们只关心 id 是否回填首位 post_id）
        assert result.platform_post_id == "111" or result.platform_post_id == "555"
        # url 字段格式
        assert "x.com" in (result.url or "") or result.url is None

    def test_dry_run_does_not_call_http(
        self, tmp_path: Path,
    ) -> None:
        conn = _conn(tmp_path)
        pub, thread_path = _seed_publication(conn, out_root=tmp_path)
        bundle = PostBundle(
            content_id=pub.content_id, title="t",
            body_path=thread_path, media_paths=(), tags=(), extra={},
        )
        # dry-run adapter 不能调 http_post
        from pipeline.publishers.x_api import XApiPublisher
        called = {"count": 0}

        def must_not_call(*a, **k):
            called["count"] += 1
            raise AssertionError("dry-run must not call http_post")

        adapter = XApiPublisher(bearer_token="dummy", http_post=must_not_call)
        result = adapter.publish(bundle, self._account(), dry_run=True)
        assert called["count"] == 0
        # 返回 dry- 前缀
        assert (result.platform_post_id or "").startswith("dry-")

    def test_mid_thread_failure_raises_publish_error(
        self, tmp_path: Path,
    ) -> None:
        """第 3 条失败 → 抛 PublishError，让编排层记录 error（含已发 id）。"""
        conn = _conn(tmp_path)
        pub, thread_path = _seed_publication(conn, out_root=tmp_path)
        bundle = PostBundle(
            content_id=pub.content_id, title="t",
            body_path=thread_path, media_paths=(), tags=(), extra={},
        )

        responses = [
            {"data": {"id": "111", "text": "t1"}},
            {"data": {"id": "222", "text": "t2"}},
            {"error": "rate limit exceeded"},
        ]
        adapter, calls = self._make_adapter(responses, bundle)
        with pytest.raises(PublishError, match="rate limit|partial"):
            adapter.publish(bundle, self._account(), dry_run=False)
        # 第 3 次 HTTP 调用发出去但抛错；前 2 条真实发出（id 已收）
        assert len(calls) == 3

    def test_publish_error_with_partial_published_ids(self, tmp_path: Path) -> None:
        """XApiPublisher 内部维护 partial_published 列表，PublishError 信息中携带。"""
        # 这里测的是 XApiPublisher 应在失败时抛出含已发 id 的 PublishError
        conn = _conn(tmp_path)
        pub, thread_path = _seed_publication(conn, out_root=tmp_path)
        bundle = PostBundle(
            content_id=pub.content_id, title="t",
            body_path=thread_path, media_paths=(), tags=(), extra={},
        )
        from pipeline.publishers.x_api import XApiPublisher

        call_count = {"n": 0}

        def fake_post(url, *, headers, body, timeout):
            call_count["n"] += 1
            if call_count["n"] <= 2:
                return {"data": {"id": f"id{call_count['n']}", "text": "x"}}
            raise PublishError("server 500")

        adapter = XApiPublisher(bearer_token="dummy", http_post=fake_post)
        try:
            adapter.publish(bundle, self._account(), dry_run=False)
            assert False, "should have raised"
        except PublishError as e:
            msg = str(e)
            # 已发 id 列表（id1, id2）应在错误信息中
            assert "id1" in msg and "id2" in msg
            assert "manual" in msg.lower() or "duplicate" in msg.lower()


# ── 与 safe_publish 联动（dry-run 端到端）────────────────


class TestSafePublishXEndToEnd:
    def test_x_dry_run_marks_published(
        self, tmp_path: Path,
    ) -> None:
        from pipeline.publishers.x_api import XApiPublisher
        from pipeline.publishers.safe_publish import safe_publish

        conn = _conn(tmp_path)
        pub, thread_path = _seed_publication(
            conn, content_id="c_xe2e", out_root=tmp_path,
        )
        adapter = XApiPublisher(
            bearer_token="dummy",
            http_post=lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("dry-run must not call http")
            ),
        )
        cfg = PublishConfig(
            enabled=True,
            allowed_platforms=["x"],
            min_gap_hours=4,
            max_daily_per_account=3,
            cross_platform_gap_minutes=30,
        )
        account = AccountConfig(id="main", credentials_path=Path("secrets/x_main.json"))
        result = safe_publish(
            conn, pub, adapter, config=cfg, account=account,
            dry_run=True, now_iso=_NOW_ISO,
        )
        assert result.published is True
        assert result.dry_run is True
        row = conn.execute(
            "SELECT status, platform_post_id FROM publications WHERE id=?",
            (pub.id,),
        ).fetchone()
        assert row["status"] == PublicationStatus.PUBLISHED.value
        assert (row["platform_post_id"] or "").startswith("dry-")

    def test_partial_thread_failure_writes_partial_to_db_error(
        self, tmp_path: Path,
    ) -> None:
        """端到端：mid-failure 后 publications.error 含 partial_published URL（人工删重帖用）。"""
        from pipeline.publishers.x_api import XApiPublisher
        from pipeline.publishers.safe_publish import safe_publish

        conn = _conn(tmp_path)
        pub, thread_path = _seed_publication(
            conn, content_id="c_xepart", out_root=tmp_path,
        )

        counter = {"n": 0}

        def fake_post(url, *, headers, body, timeout):
            counter["n"] += 1
            if counter["n"] <= 2:
                return {"data": {"id": f"id{counter['n']}", "text": "x"}}
            raise PublishError("server 500")

        adapter = XApiPublisher(bearer_token="dummy", http_post=fake_post)
        cfg = PublishConfig(
            enabled=True, allowed_platforms=["x"],
            min_gap_hours=4, max_daily_per_account=3,
            cross_platform_gap_minutes=30,
        )
        account = AccountConfig(id="main", credentials_path=Path("secrets/x_main.json"))
        result = safe_publish(
            conn, pub, adapter, config=cfg, account=account,
            dry_run=False, now_iso=_NOW_ISO,
        )

        assert result.published is False
        row = conn.execute(
            "SELECT status, error FROM publications WHERE id=?",
            (pub.id,),
        ).fetchone()
        assert row["status"] == PublicationStatus.FAILED.value
        err = row["error"] or ""
        assert "partial_published" in err
        # 已发 2 条 URL 应在 error 字段里
        assert "https://x.com/i/status/id1" in err
        assert "https://x.com/i/status/id2" in err
        assert "manual cleanup" in err.lower()


# ── LoginExpired（401/403 → 抛 LoginExpired 让编排层停该平台）───


class TestLoginExpired:
    def test_401_raises_login_expired(self, tmp_path: Path) -> None:
        """契约：401/403 → LoginExpired（PublishError 子类）—— §2 风控决策。"""
        from pipeline.publishers.base import LoginExpired
        from pipeline.publishers.x_api import XApiPublisher, _httpx_post

        # 注入 fake httpx：401
        from unittest.mock import patch, MagicMock

        fake_resp = MagicMock()
        fake_resp.status_code = 401
        fake_resp.text = "Unauthorized"

        fake_client = MagicMock()
        fake_client.post.return_value = fake_resp

        with patch("httpx.post", fake_client.post):
            try:
                _httpx_post(
                    "https://api.twitter.com/2/tweets",
                    headers={}, body={}, timeout=10,
                )
                assert False, "should have raised"
            except LoginExpired as e:
                # 401 → LoginExpired；要 stop all X tasks
                assert "auth failed" in str(e).lower() or "401" in str(e)

    def test_5xx_raises_publish_error_not_login_expired(self) -> None:
        """非 401/403 的 4xx/5xx → 业务 PublishError（不让编排层误停平台）。"""
        from pipeline.publishers.base import LoginExpired
        from pipeline.publishers.x_api import _httpx_post
        from unittest.mock import patch, MagicMock

        fake_resp = MagicMock()
        fake_resp.status_code = 503
        fake_resp.text = "Service Unavailable"

        fake_client = MagicMock()
        fake_client.post.return_value = fake_resp

        with patch("httpx.post", fake_client.post):
            try:
                _httpx_post("u", headers={}, body={}, timeout=10)
                assert False, "should have raised"
            except PublishError as e:
                assert not isinstance(e, LoginExpired)
                assert "503" in str(e)


# ── partial 文案统一性 ──────────────────────────────────────


class TestPartialMsgFormat:
    def test_partial_msg_includes_url_and_kind(self) -> None:
        """partial 错误信息含 kind + URL + 'manual cleanup' 关键字。"""
        from pipeline.publishers.x_api import _partial_msg

        published = [("id1", "https://x.com/i/status/id1")]
        msg = _partial_msg(
            failed_at=3, total=5, published=published,
            cause=ValueError("boom"), kind="X API failed",
        )
        assert "X API failed" in msg
        assert "3/5" in msg
        assert "https://x.com/i/status/id1" in msg
        assert "manual cleanup" in msg.lower()

    def test_partial_msg_empty_published(self) -> None:
        from pipeline.publishers.x_api import _partial_msg
        msg = _partial_msg(
            failed_at=1, total=3, published=[],
            cause=ValueError("boom"), kind="X API failed",
        )
        assert "partial_published=[]" in msg
