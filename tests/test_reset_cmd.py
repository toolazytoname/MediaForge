"""M8 S8-2 测试：补实 `reset` 子命令（唯一允许的逆向操作）。

行为契约（TECH_SPEC §2 + §4 + HARD_PARTS §10 第 3 条）：
  - 按 id 前缀分发表：t_→topics / c_→contents / p_→publications
  - 走 db.transition 状态机（**绝不裸 UPDATE**绕过转移表）
  - 非法逆向（不在转移表中）→ exit 1 + DB 不变 + 清晰错误
  - 不存在 id → exit 1 + DB 不变
  - 审计日志含 stage=reset + ref_id=<id>（§8 要求带 stage+ref_id）
  - reset 不触发任何发布/创作副作用（只改 status）
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from pipeline import db
from pipeline.models import (
    Content,
    ContentStatus,
    Publication,
    PublicationStatus,
    Topic,
    TopicStatus,
)


# ── fixtures ────────────────────────────────────────────────


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    """构造空 state.db（含 schema）。"""
    db_path = tmp_path / "state.db"
    c = db.connect(db_path)
    db.init_db(c)
    c.close()
    return db_path


def _patch_db_path(monkeypatch: pytest.MonkeyPatch, tmp_db_path: Path) -> None:
    import pipeline.run as run_mod
    monkeypatch.setattr(run_mod, "_DB_PATH", str(tmp_db_path))


def _now() -> str:
    return "2026-07-01T00:00:00+00:00"


def _make_pub(*, id: str, status: str) -> Publication:
    return Publication(
        id=id,
        content_id="c_seed0001",
        platform="x",
        account_id="main",
        scheduled_at=_now(),
        published_at=None,
        platform_post_id=None,
        platform_url=None,
        error=None,
        retry_count=0,
        status=status,
        created_at=_now(),
        updated_at=_now(),
    )


def _seed_pub_chain(conn, *, pub_id: str, pub_status: str) -> None:
    """插一条 publication 所需的 parent chain（FK 约束）。"""
    db.insert_topic(conn, _make_topic(
        id="t_seed0001", status=TopicStatus.CONSUMED.value,
    ))
    db.insert_content(conn, _make_content(
        id="c_seed0001", status=ContentStatus.APPROVED.value,
    ))
    db.insert_publication(conn, _make_pub(
        id=pub_id, status=pub_status,
    ))


def _make_content(*, id: str, status: str) -> Content:
    return Content(
        id=id,
        topic_id="t_seed0001",
        pillar="ai_daily",
        title="t",
        canonical_path="output/2026-07-01/x/canonical.md",
        formats=(),
        gate_score_total=None,
        gate_scores=None,
        gate_verdict=None,
        status=status,
        created_at=_now(),
        updated_at=_now(),
    )


def _make_topic(*, id: str, status: str) -> Topic:
    return Topic(
        id=id,
        source="manual",
        title="t",
        url=None,
        summary=None,
        content_hash="a" * 64,
        pillar=None,
        score=None,
        score_reason=None,
        status=status,
        created_at=_now(),
        updated_at=_now(),
    )


def _args(id: str, status: str):
    """构造 argparse.Namespace 替身。"""
    return type("A", (), {"id": id, "status": status})()


# ── 1. 合法逆向：failed → queued（§4 唯一允许的逆向） ──


class TestLegalReset:
    def test_publication_failed_to_queued_ok(
        self, monkeypatch, tmp_db_path, capsys,
    ) -> None:
        """publication failed→queued 合法（§4 转移表允许的唯一逆向）。"""
        _patch_db_path(monkeypatch, tmp_db_path)
        conn = db.connect(tmp_db_path)
        try:
            _seed_pub_chain(conn, pub_id="p_aaaa1111",
                            pub_status=PublicationStatus.FAILED.value)
        finally:
            conn.close()

        from pipeline.run import cmd_reset
        rc = cmd_reset(_args("p_aaaa1111", "queued"))
        assert rc == 0

        out = capsys.readouterr().out
        assert "ok" in out
        assert "failed" in out and "queued" in out

        # DB 状态变化
        conn = db.connect(tmp_db_path)
        try:
            row = db.get_publication(conn, "p_aaaa1111")
            assert row is not None
            assert row.status == "queued"
        finally:
            conn.close()


# ── 2. 非法逆向：转移表未定义此边 → exit 1 + DB 不变 ──


class TestIllegalReset:
    def test_publication_published_to_queued_rejected(
        self, monkeypatch, tmp_db_path, capsys,
    ) -> None:
        """published→queued 非法（转移表无此边）。"""
        _patch_db_path(monkeypatch, tmp_db_path)
        conn = db.connect(tmp_db_path)
        try:
            _seed_pub_chain(conn, pub_id="p_aaaa1111",
                            pub_status=PublicationStatus.PUBLISHED.value)
        finally:
            conn.close()

        from pipeline.run import cmd_reset
        rc = cmd_reset(_args("p_aaaa1111", "queued"))
        assert rc == 1

        out = capsys.readouterr().out
        assert "不允许" in out

        # DB 不变（仍是 published）
        conn = db.connect(tmp_db_path)
        try:
            row = db.get_publication(conn, "p_aaaa1111")
            assert row.status == "published"
        finally:
            conn.close()

    def test_publication_queued_to_failed_rejected(
        self, monkeypatch, tmp_db_path, capsys,
    ) -> None:
        """queued→failed 非法（必须经过 publishing 中间态）。"""
        _patch_db_path(monkeypatch, tmp_db_path)
        conn = db.connect(tmp_db_path)
        try:
            _seed_pub_chain(conn, pub_id="p_aaaa1111",
                            pub_status=PublicationStatus.QUEUED.value)
        finally:
            conn.close()

        from pipeline.run import cmd_reset
        rc = cmd_reset(_args("p_aaaa1111", "failed"))
        assert rc == 1
        out = capsys.readouterr().out
        assert "不允许" in out


# ── 3. 不存在 id / 非法前缀 → exit 1 ─────────────────


class TestNonExistent:
    def test_unknown_id_rejected(
        self, monkeypatch, tmp_db_path, capsys,
    ) -> None:
        _patch_db_path(monkeypatch, tmp_db_path)
        from pipeline.run import cmd_reset
        rc = cmd_reset(_args("p_doesnotexist", "queued"))
        assert rc == 1
        out = capsys.readouterr().out
        assert "不存在" in out

    def test_invalid_prefix_rejected(
        self, monkeypatch, tmp_db_path, capsys,
    ) -> None:
        _patch_db_path(monkeypatch, tmp_db_path)
        from pipeline.run import cmd_reset
        rc = cmd_reset(_args("x_abc12345", "queued"))
        assert rc == 1
        out = capsys.readouterr().out
        assert "前缀" in out or "非法" in out


# ── 4. 审计日志含 stage=reset + ref_id=<id> ──


class TestAuditLog:
    def test_audit_log_warning_contains_stage_and_ref(
        self, monkeypatch, tmp_db_path, caplog,
    ) -> None:
        """reset 成功必须写一条 stage=reset + ref_id=<id> 的 warning。"""
        _patch_db_path(monkeypatch, tmp_db_path)
        conn = db.connect(tmp_db_path)
        try:
            _seed_pub_chain(conn, pub_id="p_aaaa1111",
                            pub_status=PublicationStatus.FAILED.value)
        finally:
            conn.close()

        from pipeline.run import cmd_reset
        with caplog.at_level(logging.WARNING):
            rc = cmd_reset(_args("p_aaaa1111", "queued"))
        assert rc == 0

        found = False
        for rec in caplog.records:
            stage = getattr(rec, "stage", None)
            ref = getattr(rec, "ref_id", None)
            if stage == "reset" and ref == "p_aaaa1111":
                found = True
                break
        assert found, (
            f"审计日志缺 stage=reset + ref_id=p_aaaa1111；"
            f"实际记录：{[(r.levelname, getattr(r, 'stage', None), getattr(r, 'ref_id', None), r.getMessage()) for r in caplog.records]}"
        )


# ── 5. 前缀分发覆盖三表 ──


class TestPrefixDispatch:
    def test_topic_prefix_routes_to_topics(
        self, monkeypatch, tmp_db_path, capsys,
    ) -> None:
        """t_ 前缀走 topics 表（用合法逆向 rejected→selected 不行，因转移表不允许；
        用 rejected→rejected 同状态也不允许；改测非法转移捕获来验证路由到了正确表）。"""
        _patch_db_path(monkeypatch, tmp_db_path)
        conn = db.connect(tmp_db_path)
        try:
            db.insert_topic(conn, _make_topic(
                id="t_aaaa1111",
                status=TopicStatus.REJECTED.value,
            ))
        finally:
            conn.close()

        from pipeline.run import cmd_reset
        # rejected → selected 非法（转移表无此边）
        rc = cmd_reset(_args("t_aaaa1111", "selected"))
        assert rc == 1
        out = capsys.readouterr().out
        assert "不允许" in out

        # DB 不变（仍是 rejected）
        conn = db.connect(tmp_db_path)
        try:
            row = db.get_topic(conn, "t_aaaa1111")
            assert row.status == "rejected"
        finally:
            conn.close()

    def test_content_prefix_routes_to_contents(
        self, monkeypatch, tmp_db_path, capsys,
    ) -> None:
        """c_ 前缀走 contents 表（approved→done 合法）。"""
        _patch_db_path(monkeypatch, tmp_db_path)
        conn = db.connect(tmp_db_path)
        try:
            db.insert_topic(conn, _make_topic(
                id="t_seed0001", status=TopicStatus.CONSUMED.value,
            ))
            db.insert_content(conn, _make_content(
                id="c_aaaa1111",
                status=ContentStatus.APPROVED.value,
            ))
        finally:
            conn.close()

        from pipeline.run import cmd_reset
        rc = cmd_reset(_args("c_aaaa1111", "done"))
        assert rc == 0

        conn = db.connect(tmp_db_path)
        try:
            row = db.get_content(conn, "c_aaaa1111")
            assert row.status == "done"
        finally:
            conn.close()


# ── 6. db.transition 必须被调用（不绕状态机） ──


class TestStateMachineEnforced:
    def test_no_bare_update_in_reset_path(
        self, monkeypatch, tmp_db_path,
    ) -> None:
        """cmd_reset 必须走 db.transition 而非裸 UPDATE 绕过转移表。

        验证法：patch db.transition 抛异常时 cmd_reset 必须捕获并 exit 1；
        若 cmd_reset 改用裸 conn.execute('UPDATE ...') 会成功绕过 patch。
        """
        _patch_db_path(monkeypatch, tmp_db_path)
        conn = db.connect(tmp_db_path)
        try:
            _seed_pub_chain(conn, pub_id="p_aaaa1111",
                            pub_status=PublicationStatus.FAILED.value)
        finally:
            conn.close()

        from pipeline.utils.errors import IllegalTransition

        def fake_transition(*args, **kwargs):
            raise IllegalTransition("publications", "failed", "queued")

        monkeypatch.setattr(db, "transition", fake_transition)

        from pipeline.run import cmd_reset
        rc = cmd_reset(_args("p_aaaa1111", "queued"))
        assert rc == 1, (
            "cmd_reset 必须走 db.transition（patch 后应失败）；"
            "若 rc==0 说明绕过了状态机（HARD_PARTS §10 第 3 条红线）"
        )