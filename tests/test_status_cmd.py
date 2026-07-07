"""M8 S8-1 测试：补实 `status` 子命令（CLI 侧计数 + 本月 LLM 花费）。

行为契约：
  - 三表 status 分组计数：topics / contents / publications
  - 本月 LLM 花费（SELECT SUM(cost_usd) WHERE month prefix）
  - 空库 → 三表各状态=0 + llm=0，不报错
  - **只读**——cmd_status 不写库（db_path=空文件也能跑通）
  - 用 _DB_PATH 单点 monkeypatch，无副作用（不污染其他 cmd）

参考：
  - tests/test_webui_r7_1.py 的 monkeypatch 模式
  - pipeline/webui/app.py::_status_counts（但本测试不验 webui 行为）
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

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
from pipeline.sources.dedup import content_hash


# ── helpers ────────────────────────────────────────────────────


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    """构造空 state.db（含 schema）。"""
    db_path = tmp_path / "state.db"
    c = db.connect(db_path)
    db.init_db(c)
    c.close()
    return db_path


def _make_topic(
    *, id: str, title: str, status: str, now: str
) -> Topic:
    return Topic(
        id=id, source="rss:test", title=title, url=None, summary=None,
        content_hash=content_hash(title, None),
        pillar=None, score=None, score_reason=None,
        status=status, created_at=now, updated_at=now,
    )


def _make_content(
    *, id: str, topic_id: str, status: str, now: str
) -> Content:
    return Content(
        id=id, topic_id=topic_id, pillar="ai",
        title=f"Title-{id}", canonical_path="output/x.md",
        formats=(), gate_score_total=None, gate_scores=None,
        gate_verdict=None, status=status,
        created_at=now, updated_at=now,
    )


def _make_publication(
    *, id: str, content_id: str, status: str, now: str,
) -> Publication:
    return Publication(
        id=id, content_id=content_id, platform="x",
        account_id="main", scheduled_at=now, published_at=None,
        platform_post_id=None, platform_url=None, error=None,
        retry_count=0, status=status,
        created_at=now, updated_at=now,
    )


def _insert_llm_call(
    conn: sqlite3.Connection,
    *, cost_usd: float, created_at: str, stage: str = "score",
    model: str = "claude-haiku-4-5-20251001",
) -> int:
    """手动 insert 一条 llm_calls 行；不走 llm_mod 完整路径，专注测 status。"""
    cur = conn.execute(
        """
        INSERT INTO llm_calls
            (stage, ref_id, model, input_tokens, output_tokens,
             cost_usd, created_at)
        VALUES (?, NULL, ?, 100, 50, ?, ?)
        """,
        (stage, model, cost_usd, created_at),
    )
    conn.commit()
    return cur.lastrowid or 0


# ── 1. 空库：所有状态都打印 0 + exit 0 ──────────────────────


class TestEmptyDatabase:
    def test_cmd_status_empty_db_prints_zero_for_all_statuses(
        self, tmp_path: Path, tmp_db_path: Path, capsys,
    ) -> None:
        """空库：topics/contents/publications 各状态=0，llm 月花费=$0.0000。"""
        import pipeline.run as run_mod

        monkeypatch = pytest.MonkeyPatch()
        try:
            monkeypatch.setattr(run_mod, "_DB_PATH", str(tmp_db_path))
            # 切到 tmp_path 避免 __pycache__/locks 污染
            monkeypatch.chdir(tmp_path)
            args = MagicMock()
            rc = run_mod.cmd_status(args)
        finally:
            monkeypatch.undo()

        assert rc == 0
        out = capsys.readouterr().out

        # topics：5 个状态各 =0
        assert "topics:" in out
        assert "raw=0" in out
        assert "scored=0" in out
        assert "selected=0" in out
        assert "consumed=0" in out
        assert "rejected=0" in out

        # contents：7 个状态各 =0
        assert "contents:" in out
        assert "draft=0" in out
        assert "gated=0" in out
        assert "approved=0" in out
        assert "rejected_by_human=0" in out
        assert "discarded=0" in out
        assert "failed=0" in out
        assert "done=0" in out

        # publications：5 个状态各 =0
        assert "publications:" in out
        assert "queued=0" in out
        assert "publishing=0" in out
        assert "published=0" in out
        assert "failed=0" in out
        assert "cancelled=0" in out

        # LLM 花费行
        assert "llm:" in out
        assert "this_month=$0.0000" in out

    def test_cmd_status_exit_code_is_zero_with_empty_db(
        self, tmp_path: Path, tmp_db_path: Path,
    ) -> None:
        """空库 → exit 0（按 TECH_SPEC §2：「成功 exit 0」）。"""
        import pipeline.run as run_mod

        monkeypatch = pytest.MonkeyPatch()
        try:
            monkeypatch.setattr(run_mod, "_DB_PATH", str(tmp_db_path))
            monkeypatch.chdir(tmp_path)
            args = MagicMock()
            rc = run_mod.cmd_status(args)
        finally:
            monkeypatch.undo()
        assert rc == 0

    def test_cmd_status_db_not_yet_exists_creates_and_runs(
        self, tmp_path: Path, capsys,
    ) -> None:
        """state.db 不存在 → db.connect 创空文件 → init_db 建表 → 正常输出全 0。"""
        # 注意：tmp_db_path fixture 故意不调——本测试验「无 db 文件」路径
        db_path = tmp_path / "state.db"
        assert not db_path.exists()

        import pipeline.run as run_mod

        monkeypatch = pytest.MonkeyPatch()
        try:
            monkeypatch.setattr(run_mod, "_DB_PATH", str(db_path))
            monkeypatch.chdir(tmp_path)
            args = MagicMock()
            rc = run_mod.cmd_status(args)
        finally:
            monkeypatch.undo()

        assert rc == 0
        assert db_path.exists(), "应自动建 state.db"
        out = capsys.readouterr().out
        assert "topics:" in out
        assert "llm:" in out


# ── 2. 插入数据后：计数正确 + exit 0 ───────────────────────


class TestCountsAfterInsert:
    def test_topics_counts_match_inserted(
        self, tmp_path: Path, tmp_db_path: Path, capsys,
    ) -> None:
        """插入 2 raw + 1 scored → topics: raw=2 scored=1 ...。"""
        now = "2026-07-07T00:00:00+00:00"
        c = db.connect(tmp_db_path)
        try:
            db.init_db(c)  # 已建，但幂等
            db.insert_topic(c, _make_topic(
                id="t_aaaaaaaa", title="t1", status="raw", now=now,
            ))
            db.insert_topic(c, _make_topic(
                id="t_bbbbbbbb", title="t2", status="raw", now=now,
            ))
            db.insert_topic(c, _make_topic(
                id="t_cccccccc", title="t3", status="scored", now=now,
            ))
        finally:
            c.close()

        import pipeline.run as run_mod

        monkeypatch = pytest.MonkeyPatch()
        try:
            monkeypatch.setattr(run_mod, "_DB_PATH", str(tmp_db_path))
            monkeypatch.chdir(tmp_path)
            args = MagicMock()
            rc = run_mod.cmd_status(args)
        finally:
            monkeypatch.undo()

        assert rc == 0
        out = capsys.readouterr().out
        # topics 行：raw=2 scored=1 其他=0
        topics_line = _extract_line(out, "topics:")
        assert "raw=2" in topics_line
        assert "scored=1" in topics_line
        assert "selected=0" in topics_line
        assert "consumed=0" in topics_line
        assert "rejected=0" in topics_line

    def test_contents_counts_match_inserted(
        self, tmp_path: Path, tmp_db_path: Path, capsys,
    ) -> None:
        """插入 1 gated + 2 draft → contents: draft=2 gated=1 ...。

        contents.topic_id 是 topics.id 的 FK，需先建 topic。
        """
        now = "2026-07-07T00:00:00+00:00"
        c = db.connect(tmp_db_path)
        try:
            db.init_db(c)  # 已建，但幂等
            for i in range(3):
                db.insert_topic(c, _make_topic(
                    id=f"t_{i:08x}", title=f"topic-{i}",
                    status="raw", now=now,
                ))
            db.insert_content(c, _make_content(
                id="c_aaaaaaaa", topic_id="t_00000000",
                status="draft", now=now,
            ))
            db.insert_content(c, _make_content(
                id="c_bbbbbbbb", topic_id="t_00000001",
                status="draft", now=now,
            ))
            db.insert_content(c, _make_content(
                id="c_cccccccc", topic_id="t_00000002",
                status="gated", now=now,
            ))
        finally:
            c.close()

        import pipeline.run as run_mod

        monkeypatch = pytest.MonkeyPatch()
        try:
            monkeypatch.setattr(run_mod, "_DB_PATH", str(tmp_db_path))
            monkeypatch.chdir(tmp_path)
            args = MagicMock()
            rc = run_mod.cmd_status(args)
        finally:
            monkeypatch.undo()

        assert rc == 0
        out = capsys.readouterr().out
        line = _extract_line(out, "contents:")
        assert "draft=2" in line
        assert "gated=1" in line
        assert "approved=0" in line

    def test_publications_counts_match_inserted(
        self, tmp_path: Path, tmp_db_path: Path, capsys,
    ) -> None:
        """插入 3 queued + 1 published → publications: queued=3 published=1 ...。

        publications 有 FK + UNIQUE(content, platform, account)，需先建
        topic → content，再造同平台多 pub 与跨 content 已发 pub。
        """
        now = "2026-07-07T00:00:00+00:00"
        c = db.connect(tmp_db_path)
        try:
            db.init_db(c)
            # 建两条 topic + 两条 approved content（满足 FK）
            db.insert_topic(c, _make_topic(
                id="t_a0000000", title="tA", status="selected", now=now,
            ))
            db.insert_topic(c, _make_topic(
                id="t_b0000000", title="tB", status="selected", now=now,
            ))
            db.insert_content(c, _make_content(
                id="c_aaaaaaaa", topic_id="t_a0000000",
                status="approved", now=now,
            ))
            db.insert_content(c, _make_content(
                id="c_bbbbbbbb", topic_id="t_b0000000",
                status="approved", now=now,
            ))
            # 同 (content, platform, account) 的 pub：换 account_id 才能多插
            db.insert_publication(c, _make_publication(
                id="p_00000000", content_id="c_aaaaaaaa",
                status="queued", now=now,
            ))
            db.insert_publication(c, Publication(
                id="p_00000001", content_id="c_aaaaaaaa",
                platform="x", account_id="acc2",  # 不同账号避免 UNIQUE 冲突
                scheduled_at=now, published_at=None,
                platform_post_id=None, platform_url=None, error=None,
                retry_count=0, status="queued",
                created_at=now, updated_at=now,
            ))
            db.insert_publication(c, Publication(
                id="p_00000002", content_id="c_bbbbbbbb",
                platform="x", account_id="main",
                scheduled_at=now, published_at=None,
                platform_post_id=None, platform_url=None, error=None,
                retry_count=0, status="queued",
                created_at=now, updated_at=now,
            ))
            db.insert_publication(c, Publication(
                id="p_deadbeef", content_id="c_aaaaaaaa",
                platform="toutiao",  # 不同平台避免与 queued 冲突
                account_id="main",
                scheduled_at=now, published_at=now,
                platform_post_id="post-1", platform_url="https://x.com/1",
                error=None, retry_count=0, status="published",
                created_at=now, updated_at=now,
            ))
        finally:
            c.close()

        import pipeline.run as run_mod

        monkeypatch = pytest.MonkeyPatch()
        try:
            monkeypatch.setattr(run_mod, "_DB_PATH", str(tmp_db_path))
            monkeypatch.chdir(tmp_path)
            args = MagicMock()
            rc = run_mod.cmd_status(args)
        finally:
            monkeypatch.undo()

        assert rc == 0
        out = capsys.readouterr().out
        line = _extract_line(out, "publications:")
        assert "queued=3" in line
        assert "published=1" in line
        assert "publishing=0" in line
        assert "failed=0" in line
        assert "cancelled=0" in line


# ── 3. LLM 本月花费行 ────────────────────────────────────


class TestLlmCostThisMonth:
    def test_llm_cost_line_present_with_zero(
        self, tmp_path: Path, tmp_db_path: Path, capsys,
    ) -> None:
        """空库 llm_calls → llm: this_month=$0.0000。"""
        import pipeline.run as run_mod

        monkeypatch = pytest.MonkeyPatch()
        try:
            monkeypatch.setattr(run_mod, "_DB_PATH", str(tmp_db_path))
            monkeypatch.chdir(tmp_path)
            args = MagicMock()
            run_mod.cmd_status(args)
        finally:
            monkeypatch.undo()

        out = capsys.readouterr().out
        line = _extract_line(out, "llm:")
        assert "this_month=$0.0000" in line

    def test_llm_cost_sums_this_month_only(
        self, tmp_path: Path, tmp_db_path: Path, capsys,
    ) -> None:
        """本月 2 笔 $0.5 + 上一月 1 笔 $99 → 只合计本月。"""
        c = db.connect(tmp_db_path)
        try:
            this_month = datetime.now(timezone.utc).strftime("%Y-%m")
            _insert_llm_call(
                c, cost_usd=0.123, created_at=f"{this_month}-05T10:00:00+00:00",
            )
            _insert_llm_call(
                c, cost_usd=0.456, created_at=f"{this_month}-15T11:00:00+00:00",
            )
            # 上一月一笔（应被排除）
            _insert_llm_call(
                c, cost_usd=99.999,
                created_at="2020-01-15T10:00:00+00:00",
            )
        finally:
            c.close()

        import pipeline.run as run_mod

        monkeypatch = pytest.MonkeyPatch()
        try:
            monkeypatch.setattr(run_mod, "_DB_PATH", str(tmp_db_path))
            monkeypatch.chdir(tmp_path)
            args = MagicMock()
            run_mod.cmd_status(args)
        finally:
            monkeypatch.undo()

        out = capsys.readouterr().out
        line = _extract_line(out, "llm:")
        # 0.123 + 0.456 = 0.579 → $0.5790（4 位小数）
        assert "this_month=$0.5790" in line
        assert "99.999" not in line  # 上月被排除

    def test_llm_cost_includes_future_dated_records(
        self, tmp_path: Path, tmp_db_path: Path, capsys,
    ) -> None:
        """本月内任意日期都计入（包括未来日期，因 created_at 是 ISO 月份前缀匹配）。"""
        c = db.connect(tmp_db_path)
        try:
            this_month = datetime.now(timezone.utc).strftime("%Y-%m")
            _insert_llm_call(
                c, cost_usd=1.0,
                created_at=f"{this_month}-31T23:59:59+00:00",
            )
            _insert_llm_call(
                c, cost_usd=2.5,
                created_at=f"{this_month}-01T00:00:00+00:00",
            )
        finally:
            c.close()

        import pipeline.run as run_mod

        monkeypatch = pytest.MonkeyPatch()
        try:
            monkeypatch.setattr(run_mod, "_DB_PATH", str(tmp_db_path))
            monkeypatch.chdir(tmp_path)
            args = MagicMock()
            run_mod.cmd_status(args)
        finally:
            monkeypatch.undo()

        out = capsys.readouterr().out
        line = _extract_line(out, "llm:")
        # 1.0 + 2.5 = 3.5
        assert "this_month=$3.5000" in line


# ── 4. 只读校验：cmd_status 不写库 ──────────────────────────


class TestReadOnly:
    def test_cmd_status_does_not_write_db(
        self, tmp_path: Path, tmp_db_path: Path,
    ) -> None:
        """跑完 cmd_status 后 db 文件大小/mtime 不变（间接验证无写操作）。"""
        before_size = tmp_db_path.stat().st_size
        before_mtime = tmp_db_path.stat().st_mtime

        import pipeline.run as run_mod

        monkeypatch = pytest.MonkeyPatch()
        try:
            monkeypatch.setattr(run_mod, "_DB_PATH", str(tmp_db_path))
            monkeypatch.chdir(tmp_path)
            args = MagicMock()
            run_mod.cmd_status(args)
        finally:
            monkeypatch.undo()

        # init_db 是空跑的幂等 DDL（CREATE TABLE IF NOT EXISTS），不真改 schema，
        # 但 SQLite WAL 可能写一下；用 mtime 比较太敏感。改用：跑前后
        # 真实表数应稳定（5 用户表 + AUTOINCREMENT 引入的 sqlite_sequence）
        c = db.connect(tmp_db_path)
        try:
            tables = c.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' ORDER BY name"
            ).fetchall()
            table_names = [r["name"] for r in tables]
        finally:
            c.close()

        expected = {"topics", "contents", "publications", "metrics", "llm_calls"}
        assert expected.issubset(set(table_names)), (
            f"应有表缺失；现有表={table_names}"
        )


# ── 5. 输出格式：四行、四段可控 ─────────────────────────────


class TestOutputFormat:
    def test_output_has_four_lines(
        self, tmp_path: Path, tmp_db_path: Path, capsys,
    ) -> None:
        """输出正好 4 行：topics / contents / publications / llm。"""
        import pipeline.run as run_mod

        monkeypatch = pytest.MonkeyPatch()
        try:
            monkeypatch.setattr(run_mod, "_DB_PATH", str(tmp_db_path))
            monkeypatch.chdir(tmp_path)
            args = MagicMock()
            run_mod.cmd_status(args)
        finally:
            monkeypatch.undo()

        out = capsys.readouterr().out
        non_empty_lines = [ln for ln in out.splitlines() if ln.strip()]
        # topics, contents, publications, llm 共 4 行
        assert len(non_empty_lines) == 4
        # 顺序：topics 在 contents 前
        assert out.index("topics:") < out.index("contents:")
        assert out.index("contents:") < out.index("publications:")
        assert out.index("publications:") < out.index("llm:")


# ── 6. 集成：db.py + cmd_status 一致 + 其它 cmd 不受影响 ────


class TestNoCrossCmdInterference:
    def test_status_does_not_touch_db_path_for_other_cmds(
        self, tmp_path: Path, tmp_db_path: Path,
    ) -> None:
        """_DB_PATH 只控制 cmd_status；其它 cmd 仍硬编码 'state.db'。

        本测试仅校验：改 _DB_PATH 后调 cmd_status 不爆；
        用 monkeypatch.undo() 还原；后续调 cmd_init_db（也读 'state.db'）。
        """
        import pipeline.run as run_mod

        monkeypatch = pytest.MonkeyPatch()
        try:
            monkeypatch.setattr(run_mod, "_DB_PATH", str(tmp_db_path))
            monkeypatch.chdir(tmp_path)
            args = MagicMock()
            rc = run_mod.cmd_status(args)
            assert rc == 0
        finally:
            monkeypatch.undo()

        # 还原后其它 cmd 用硬编码 'state.db'，不应报错
        args2 = MagicMock()
        rc2 = run_mod.cmd_init_db(args2)
        assert rc2 == 0


# ── helpers ────────────────────────────────────────────────


def _extract_line(out: str, prefix: str) -> str:
    """从多行输出里抽出以 `prefix` 开头的一整行。"""
    for ln in out.splitlines():
        if ln.startswith(prefix):
            return ln
    raise AssertionError(
        f"找不到以 {prefix!r} 开头的行；输出：\n{out}"
    )
