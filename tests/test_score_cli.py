"""topics/runner.py::score_all 集成测试（M1-4 验收）。

行为契约：
  - 加载 config（pillars + topics.min_score / daily_quota + llm.tiers）
  - 注入 llm 模块级状态（provider / tier_map / db conn）
  - 对每条 raw topic 调 score_topic（失败→rejected，不阻断）
  - select_daily 转 top N → selected
  - 返回 ScoreRunResult(processed, selected, rejected) 给 CLI 打摘要
  - 二次运行 → 0/0/0（幂等）
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from pipeline import db
from pipeline.config import Pillar
from pipeline.creators import llm as llm_mod
from pipeline.creators.llm import (
    CompletionResult,
    LLMProvider,
    set_provider,
)
from pipeline.models import TopicStatus
from pipeline.sources.dedup import content_hash
from pipeline.topics import runner
from pipeline.utils.ids import new_id


# ── helpers ──────────────────────────────────────────

class ScriptedProvider(LLMProvider):
    def __init__(self, responses: list[str], *, fail_remaining: bool = False):
        self._responses = list(responses)
        self._fail_remaining = fail_remaining
        self.calls: list[dict] = []

    def call(self, prompt, model, max_tokens):
        self.calls.append({"prompt": prompt, "model": model})
        if self._fail_remaining:
            raise llm_mod.RetryableError("429 mocked")
        if not self._responses:
            raise llm_mod.RetryableError("no more scripted")
        return CompletionResult(
            text=self._responses.pop(0),
            input_tokens=50, output_tokens=20,
        )


@pytest.fixture
def db_with_raw(tmp_path: Path) -> sqlite3.Connection:
    p = tmp_path / "state.db"
    conn = db.connect(p)
    db.init_db(conn)
    seeds = [
        ("good-1", "GPT-5 released"),
        ("good-2", "Llama 4 dropped"),
        ("good-3", "New RLHF paper"),
        ("good-4", "Open source gem"),
    ]
    now = "2026-07-05T00:00:00+00:00"
    for src, title in seeds:
        t = _make_topic(id=new_id("t"), title=title, source=src, now=now)
        db.insert_topic(conn, t)
    return conn


def _make_topic(*, id: str, title: str, source: str, now: str):
    from pipeline.models import Topic

    return Topic(
        id=id, source=source, title=title, url=None, summary=None,
        content_hash=content_hash(title, None),
        pillar=None, score=None, score_reason=None,
        status=TopicStatus.RAW.value,
        created_at=now, updated_at=now,
    )


def _pillars():
    return [
        Pillar(id="ai", name="AI", description="AI news", scoring_hint="时效"),
        Pillar(id="oss", name="OSS", description="open source", scoring_hint="star"),
    ]


@pytest.fixture(autouse=True)
def reset_llm_state():
    set_provider(ScriptedProvider([]))
    yield
    set_provider(ScriptedProvider([]))


# ── 端到端 ─────────────────────────────────────────

def test_end_to_end_score_and_select(db_with_raw) -> None:
    """4 条 raw → 评分 → 选 top 2 (quota=2, min_score=6.0)。"""
    conn = db_with_raw
    set_provider(ScriptedProvider([
        json.dumps({"pillar": "ai", "score": 8.5, "reason": "good"}),
        json.dumps({"pillar": "ai", "score": 5.0, "reason": "ok"}),
        json.dumps({"pillar": "ai", "score": 9.0, "reason": "great"}),
        json.dumps({"pillar": "oss", "score": 7.0, "reason": "useful"}),
    ]))

    result = runner.score_all(
        conn, pillars=_pillars(),
        quota=2, min_score=6.0,
        now="2026-07-05T02:00:00+00:00",
    )

    assert result.processed == 4
    assert result.selected == 2
    assert result.rejected == 0  # 都解析成功

    # 状态分布：2 selected + 2 scored (5.0 & 7.0 quota 已满)
    statuses = [
        r["status"]
        for r in conn.execute("SELECT status FROM topics").fetchall()
    ]
    assert statuses.count(TopicStatus.SELECTED.value) == 2
    assert statuses.count(TopicStatus.SCORED.value) == 2


def test_idempotent_second_run(db_with_raw) -> None:
    """二次跑：processed=0，selected=0（无 raw，且上轮留下的也全 <min_score）。"""
    conn = db_with_raw
    # 全 4 条都评 5.0（< min_score 6.0）→ 第一轮全保持 scored，0 selected
    set_provider(ScriptedProvider([
        json.dumps({"pillar": "ai", "score": 5.0, "reason": "low"}),
        json.dumps({"pillar": "ai", "score": 5.0, "reason": "low"}),
        json.dumps({"pillar": "ai", "score": 5.0, "reason": "low"}),
        json.dumps({"pillar": "ai", "score": 5.0, "reason": "low"}),
    ]))

    first = runner.score_all(
        conn, pillars=_pillars(), quota=2, min_score=6.0,
        now="2026-07-05T02:00:00+00:00",
    )
    second = runner.score_all(
        conn, pillars=_pillars(), quota=2, min_score=6.0,
        now="2026-07-05T03:00:00+00:00",
    )

    assert first.processed == 4
    assert first.selected == 0  # 全 < min_score
    assert second.processed == 0  # 无 raw
    assert second.selected == 0  # 留下的 scored 也全 < min_score


def test_all_retryable_failure_rejects_all(db_with_raw) -> None:
    """LLM 全部重试用尽 → 全 rejected。"""
    conn = db_with_raw
    set_provider(ScriptedProvider([], fail_remaining=True))

    result = runner.score_all(
        conn, pillars=_pillars(), quota=2, min_score=6.0,
        now="2026-07-05T02:00:00+00:00",
    )

    assert result.processed == 4
    assert result.rejected == 4
    assert result.selected == 0
    for row in conn.execute("SELECT status FROM topics").fetchall():
        assert row["status"] == TopicStatus.REJECTED.value


def test_llm_call_recorded(db_with_raw) -> None:
    """每次评分调用都写一条 llm_calls 行（ref_id = topic.id）。"""
    conn = db_with_raw
    set_provider(ScriptedProvider([
        json.dumps({"pillar": "ai", "score": 8.0, "reason": "ok"}),
        json.dumps({"pillar": "ai", "score": 8.0, "reason": "ok"}),
        json.dumps({"pillar": "ai", "score": 8.0, "reason": "ok"}),
        json.dumps({"pillar": "ai", "score": 8.0, "reason": "ok"}),
    ]))

    runner.score_all(
        conn, pillars=_pillars(), quota=2, min_score=6.0,
        now="2026-07-05T02:00:00+00:00",
    )

    rows = conn.execute("SELECT * FROM llm_calls").fetchall()
    assert len(rows) == 4
    for row in rows:
        assert row["stage"] == "score"
        assert row["model"] == "claude-haiku-4-5-20251001"
        assert row["cost_usd"] > 0


# ── M1-6 跨源 URL 去重（score runner 集成） ──────────────────


def _insert_topic_with_url(
    conn, *, id: str, title: str, url: str | None, summary: str = "",
    now: str,
) -> None:
    """插入带 URL 的 raw topic（绕过 _make_topic 的固定 url=None）。"""
    from pipeline.models import Topic

    t = Topic(
        id=id, source="rss:test", title=title, url=url, summary=summary,
        content_hash=content_hash(title, url),
        pillar=None, score=None, score_reason=None,
        status=TopicStatus.RAW.value,
        created_at=now, updated_at=now,
    )
    db.insert_topic(conn, t)


def test_url_dedup_merges_duplicates_in_runner(tmp_path: Path) -> None:
    """同 URL 两条 → runner 只评 1 条代表（processed=1，duplicates_merged=1）。

    LLM 调用次数也从 2 降到 1（避免重复占用 daily_quota）。
    """
    p = tmp_path / "state.db"
    conn = db.connect(p)
    db.init_db(conn)
    now = "2026-07-05T00:00:00+00:00"
    _insert_topic_with_url(
        conn, id="t1", title="Short", url="https://example.com/a",
        now=now,
    )
    _insert_topic_with_url(
        conn, id="t2", title="Longer title for same news",
        url="https://example.com/a", now=now,
    )

    set_provider(ScriptedProvider([
        json.dumps({"pillar": "ai", "score": 8.0, "reason": "ok"}),
    ]))

    result = runner.score_all(
        conn, pillars=_pillars(), quota=2, min_score=6.0,
        now="2026-07-05T02:00:00+00:00",
    )

    assert result.processed == 1  # 只评代表条
    assert result.duplicates_merged == 1
    assert result.selected == 1

    # LLM 只调 1 次（不是 2 次）
    rows = conn.execute("SELECT * FROM llm_calls").fetchall()
    assert len(rows) == 1


def test_url_dedup_no_merge_when_urls_differ(tmp_path: Path) -> None:
    """URL 不同 → 不合并，processed=2，duplicates_merged=0。"""
    p = tmp_path / "state.db"
    conn = db.connect(p)
    db.init_db(conn)
    now = "2026-07-05T00:00:00+00:00"
    _insert_topic_with_url(
        conn, id="t1", title="A", url="https://a.com/1", now=now,
    )
    _insert_topic_with_url(
        conn, id="t2", title="B", url="https://b.com/2", now=now,
    )

    set_provider(ScriptedProvider([
        json.dumps({"pillar": "ai", "score": 8.0, "reason": "ok"}),
        json.dumps({"pillar": "ai", "score": 8.0, "reason": "ok"}),
    ]))

    result = runner.score_all(
        conn, pillars=_pillars(), quota=2, min_score=6.0,
        now="2026-07-05T02:00:00+00:00",
    )

    assert result.processed == 2
    assert result.duplicates_merged == 0


def test_url_dedup_no_url_topics_pass_through(tmp_path: Path) -> None:
    """url=None 的 topic 不参与合并（无 key），全部 processed。"""
    p = tmp_path / "state.db"
    conn = db.connect(p)
    db.init_db(conn)
    now = "2026-07-05T00:00:00+00:00"
    # 复用 db_with_raw fixture 的 4 条（都是 url=None）
    seeds = ["a", "b", "c", "d"]
    for s in seeds:
        t = _make_topic(id=new_id("t"), title=s, source=f"src-{s}", now=now)
        db.insert_topic(conn, t)

    set_provider(ScriptedProvider([
        json.dumps({"pillar": "ai", "score": 8.0, "reason": "ok"}),
        json.dumps({"pillar": "ai", "score": 8.0, "reason": "ok"}),
        json.dumps({"pillar": "ai", "score": 8.0, "reason": "ok"}),
        json.dumps({"pillar": "ai", "score": 8.0, "reason": "ok"}),
    ]))

    result = runner.score_all(
        conn, pillars=_pillars(), quota=2, min_score=6.0,
        now="2026-07-05T02:00:00+00:00",
    )

    assert result.processed == 4  # 全评（无 URL 不合并）
    assert result.duplicates_merged == 0


def test_url_dedup_logs_warning_when_dup_found(
    tmp_path: Path, capsys,
) -> None:
    """有 duplicate → 打 stderr 摘要 'M1-6 merged N duplicate(s)'。"""
    p = tmp_path / "state.db"
    conn = db.connect(p)
    db.init_db(conn)
    now = "2026-07-05T00:00:00+00:00"
    _insert_topic_with_url(
        conn, id="t1", title="A", url="https://example.com/a", now=now,
    )
    _insert_topic_with_url(
        conn, id="t2", title="B", url="https://example.com/a", now=now,
    )

    set_provider(ScriptedProvider([
        json.dumps({"pillar": "ai", "score": 8.0, "reason": "ok"}),
    ]))

    runner.score_all(
        conn, pillars=_pillars(), quota=2, min_score=6.0,
        now="2026-07-05T02:00:00+00:00",
    )

    captured = capsys.readouterr()
    assert "M1-6 merged 1 duplicate(s)" in captured.err