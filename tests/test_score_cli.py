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