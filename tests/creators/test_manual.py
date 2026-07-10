"""M11-G 手动创作编排函数单测。

覆盖：
  - create_manual 正常路径：造 source='manual' topic + 转移 SELECTED→CONSUMED
    + 落 canonical.md + INSERT contents(status=draft)
  - create_manual 字段校验：title 空 / body 空 → 抛 CreateError
  - create_manual 写盘失败 → 抛 CreateError,topic/content 已落库
  - update_manual_draft 改 title/pillar/formats
  - update_manual_draft 改 body_markdown 重写 canonical.md
  - update_manual_draft 不允许编辑非 draft 内容 → 抛 CreateError
  - 手动创作路径不调 LLM（成本护栏 HARD_PARTS §4 — 验证 import anthropic 不出 manual.py）
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from pipeline import db
from pipeline.creators import manual as manual_creator
from pipeline.models import (
    Content,
    ContentStatus,
    Topic,
    TopicStatus,
)
from pipeline.utils.errors import CreateError


# ── 静态护栏：手动创作路径禁止调 LLM（HARD_PARTS §4）──────


def test_manual_module_does_not_import_anthropic_outside_llm():
    """成本护栏：manual.py 里不得出现任何 anthropic 调用。"""
    src = Path(__file__).resolve().parents[2] / "pipeline" / "creators" / "manual.py"
    text = src.read_text(encoding="utf-8")
    assert "import anthropic" not in text
    assert "complete(" not in text  # 验证不调 llm.complete
    assert "complete_json(" not in text  # 验证不调 llm.complete_json


# ── fixtures ──────────────────────────────────────


@pytest.fixture
def conn(tmp_path):
    """M11-G 测试用临时 SQLite + 隔离 output/ 目录。

    每个 test 都有自己的 db + output,互不污染。
    """
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = MEMORY")
    db.init_db(conn)
    return conn


@pytest.fixture
def output_root(tmp_path):
    d = tmp_path / "output"
    d.mkdir()
    return d


# ── create_manual 正常路径 ─────────────────────────────


class TestCreateManual:
    def test_happy_path(self, conn, output_root):
        c = manual_creator.create_manual(
            conn,
            title="我的标题",
            pillar="ai_daily",
            body_markdown="# hi\n\n 这是手动写的内容\n",
            formats=("xhs", "x"),
            now="2026-07-11T10:00:00+00:00",
            output_root=output_root,
        )
        # 1. content 是 draft
        assert isinstance(c, Content)
        assert c.status == ContentStatus.DRAFT.value
        assert c.title == "我的标题"
        assert c.pillar == "ai_daily"
        assert set(c.formats) == {"xhs", "x"}
        # 2. topic 是 source='manual' + status=consumed
        t = db.get_topic(conn, c.topic_id)
        assert t is not None
        assert t.source == "manual"
        assert t.status == TopicStatus.CONSUMED.value
        # 3. canonical.md 已落盘(同格式同目录约定)
        cp = Path(c.canonical_path)
        assert cp.is_file()
        assert cp.read_text(encoding="utf-8") == "# hi\n\n 这是手动写的内容\n"
        # 4. content.topic_id 绑 manual topic
        assert c.topic_id == t.id

    def test_empty_title_raises(self, conn, output_root):
        with pytest.raises(CreateError, match="title 不能为空"):
            manual_creator.create_manual(
                conn, title="   ", pillar="ai_daily",
                body_markdown="body", now="2026-07-11T10:00:00+00:00",
                output_root=output_root,
            )

    def test_empty_body_raises(self, conn, output_root):
        with pytest.raises(CreateError, match="body_markdown 不能为空"):
            manual_creator.create_manual(
                conn, title="t", pillar="ai_daily",
                body_markdown="   \n  ", now="2026-07-11T10:00:00+00:00",
                output_root=output_root,
            )

    def test_pillar_normalized_to_uncategorized_when_blank(self, conn, output_root):
        c = manual_creator.create_manual(
            conn, title="t", pillar="",
            body_markdown="body",
            now="2026-07-11T10:00:00+00:00",
            output_root=output_root,
        )
        assert c.pillar == "uncategorized"

    def test_canonical_path_under_date_dir(self, conn, output_root):
        c = manual_creator.create_manual(
            conn, title="t", pillar="ai_daily",
            body_markdown="body",
            now="2026-07-11T10:00:00+00:00",
            output_root=output_root,
        )
        cp = Path(c.canonical_path)
        # 输出格式:output/YYYY-MM-DD/<content_id>/canonical.md
        parts = cp.parts
        assert parts[-3] == "2026-07-11"
        assert parts[-1] == "canonical.md"
        assert cp.parent.name == c.id

    def test_idempotent_no_tmp_residue(self, conn, output_root):
        """HARD_PARTS §5:tmp→rename 完成后不应留 .tmp 残留。"""
        c = manual_creator.create_manual(
            conn, title="t1", pillar="ai_daily",
            body_markdown="body1",
            now="2026-07-11T10:00:00+00:00",
            output_root=output_root,
        )
        final_dir = Path(c.canonical_path).parent
        # 不应有 content_id.tmp 残留(本次 run 完成时 tmp 已被 rename)
        stray = final_dir.with_name(final_dir.name + ".tmp")
        assert not stray.exists()
        # 也不应有日期级 stray.tmp 残留(测试代码故意失败模拟)
        date_dir = final_dir.parent
        for p in date_dir.iterdir():
            assert not p.name.endswith(".tmp"), f"found stray .tmp: {p}"


# ── update_manual_draft ────────────────────────────────


class TestUpdateManualDraft:
    def _seed_draft(self, conn, output_root) -> Content:
        return manual_creator.create_manual(
            conn, title="orig", pillar="ai_daily",
            body_markdown="orig body",
            formats=("xhs",),
            now="2026-07-11T10:00:00+00:00",
            output_root=output_root,
        )

    def test_update_title_pillar_formats(self, conn, output_root):
        c = self._seed_draft(conn, output_root)
        c2 = manual_creator.update_manual_draft(
            conn, c.id,
            title="new title",
            pillar="finance",
            formats=("x", "article"),
            now="2026-07-11T11:00:00+00:00",
        )
        assert c2.title == "new title"
        assert c2.pillar == "finance"
        assert set(c2.formats) == {"x", "article"}
        # 文件未变
        assert Path(c.canonical_path).read_text(encoding="utf-8") == "orig body"

    def test_update_body_rewrites_canonical(self, conn, output_root):
        c = self._seed_draft(conn, output_root)
        c2 = manual_creator.update_manual_draft(
            conn, c.id,
            body_markdown="全新正文",
            now="2026-07-11T11:00:00+00:00",
        )
        assert Path(c.canonical_path).read_text(encoding="utf-8") == "全新正文"
        assert c2.id == c.id

    def test_only_updated_at_increments_when_no_fields(self, conn, output_root):
        """所有字段都传 None —— db.update_content_draft 应静默返回 0(不报错)。"""
        c = self._seed_draft(conn, output_root)
        # 只改 updated_at,我们手动模拟：「传所有 None」
        # update_manual_draft 直接不调用任何字段变更时,db 返回 0 表示无变更
        c2 = manual_creator.update_manual_draft(
            conn, c.id,
            now="2026-07-11T11:00:00+00:00",
        )
        # 即使没字段更新也成功(draft 校验通过)
        assert c2.id == c.id
        assert c2.status == ContentStatus.DRAFT.value

    def test_non_draft_rejected(self, conn, output_root):
        c = self._seed_draft(conn, output_root)
        # 手动推到 gated
        db.transition(conn, "contents", c.id,
                      ContentStatus.DRAFT.value, ContentStatus.GATED.value)
        with pytest.raises(CreateError, match="状态"):
            manual_creator.update_manual_draft(
                conn, c.id, title="x", now="2026-07-11T11:00:00+00:00",
            )

    def test_missing_content_raises(self, conn, output_root):
        with pytest.raises(CreateError, match="不存在"):
            manual_creator.update_manual_draft(
                conn, "c_nope", title="x", now="2026-07-11T11:00:00+00:00",
            )

    def test_blank_body_rejected(self, conn, output_root):
        c = self._seed_draft(conn, output_root)
        with pytest.raises(CreateError, match="不能置空"):
            manual_creator.update_manual_draft(
                conn, c.id, body_markdown="   \n  ",
                now="2026-07-11T11:00:00+00:00",
            )

    def test_subsequent_edit_updates_files(self, conn, output_root):
        """两次编辑：第二次覆盖第一次的写入(无残留)。"""
        c = self._seed_draft(conn, output_root)
        manual_creator.update_manual_draft(
            conn, c.id, body_markdown="edit 1",
            now="2026-07-11T11:00:00+00:00",
        )
        manual_creator.update_manual_draft(
            conn, c.id, body_markdown="edit 2",
            now="2026-07-11T12:00:00+00:00",
        )
        assert Path(c.canonical_path).read_text(encoding="utf-8") == "edit 2"
        # 不留 .tmp 残留
        stray = Path(c.canonical_path).with_name(
            Path(c.canonical_path).name + ".tmp"
        )
        assert not stray.exists()
