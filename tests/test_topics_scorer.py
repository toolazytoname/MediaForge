"""topics/scorer.py 单元测试（M1-4）。

行为契约：
  - 给 topic + pillars → build prompt → complete() cheap → 解析 JSON
  - JSON 解析失败 → 重试 1 次 → 仍失败抛 ScoreParseError
  - 评分 + 状态转移（raw→scored 或 raw→rejected）同事务（HARD_PARTS §5）
  - 同一 topic 不会被评两次（不在 raw 中的跳过）
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline import db
from pipeline.config import Pillar
from pipeline.creators import llm as llm_mod
from pipeline.creators.llm import (
    CompletionResult,
    LLMProvider,
    set_provider,
)
from pipeline.models import Topic, TopicStatus
from pipeline.topics import scorer


# ── helpers ──────────────────────────────────────────────

class ScriptedProvider(LLMProvider):
    """脚本化 provider：按调用顺序返回预设响应。"""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def call(self, prompt, model, max_tokens):
        self.calls.append({"prompt": prompt, "model": model})
        if not self._responses:
            raise RuntimeError("no more scripted responses")
        return CompletionResult(
            text=self._responses.pop(0),
            input_tokens=100,
            output_tokens=50,
        )


def _open_db(tmp_path: Path) -> sqlite3.Connection:
    p = tmp_path / "state.db"
    c = db.connect(p)
    db.init_db(c)
    return c


def _insert_raw(
    conn, *, id: str, title: str, source: str = "rss:test",
    summary: str | None = None,
) -> Topic:
    from pipeline.sources.dedup import content_hash

    h = content_hash(title, None)
    topic = Topic(
        id=id, source=source, title=title, url=None,
        summary=summary, content_hash=h,
        pillar=None, score=None, score_reason=None,
        status=TopicStatus.RAW.value,
        created_at="2026-07-05T00:00:00+00:00",
        updated_at="2026-07-05T00:00:00+00:00",
    )
    db.insert_topic(conn, topic)
    return topic


@pytest.fixture(autouse=True)
def reset_provider():
    set_provider(ScriptedProvider([]))
    yield


def _pillars() -> list[Pillar]:
    return [
        Pillar(
            id="ai", name="AI/科技",
            description="AI 领域动态",
            scoring_hint="时效性 + 工程师视角加分",
        ),
        Pillar(
            id="oss", name="开源测评",
            description="值得关注的开源项目",
            scoring_hint="star 增速 + 可上手",
        ),
    ]


# ── 正常路径 ──────────────────────────────────────────

def test_score_topic_writes_score_and_transitions(tmp_path) -> None:
    """正常 LLM 返回 → 写入 pillar/score/reason + raw→scored。"""
    conn = _open_db(tmp_path)
    topic = _insert_raw(conn, id="t_aaa00001", title="GPT-5 released")
    set_provider(ScriptedProvider([
        json.dumps({"pillar": "ai", "score": 8.5, "reason": "good"})
    ]))

    result = scorer.score_topic(
        conn, topic, pillars=_pillars(), now="2026-07-05T01:00:00+00:00"
    )

    assert result.pillar == "ai"
    assert result.score == 8.5
    assert result.reason == "good"
    assert result.accepted is True

    row = conn.execute("SELECT * FROM topics WHERE id=?", ("t_aaa00001",)).fetchone()
    assert row["score"] == 8.5
    assert row["pillar"] == "ai"
    assert row["score_reason"] == "good"
    assert row["status"] == TopicStatus.SCORED.value


def test_uses_cheap_tier(tmp_path) -> None:
    """score 阶段走 cheap 档（HARD_PARTS §4 控成本）。"""
    conn = _open_db(tmp_path)
    topic = _insert_raw(conn, id="t_aaa00002", title="x")
    prov = ScriptedProvider([
        json.dumps({"pillar": "ai", "score": 7.0, "reason": "ok"})
    ])
    set_provider(prov)

    scorer.score_topic(
        conn, topic, pillars=_pillars(), now="2026-07-05T01:00:00+00:00"
    )

    assert prov.calls[0]["model"] == "claude-haiku-4-5-20251001"


def test_prompt_includes_topic_and_pillars(tmp_path) -> None:
    """prompt 必须含 topic 标题 + 各 pillar 的 name/description/hint。"""
    conn = _open_db(tmp_path)
    topic = _insert_raw(
        conn, id="t_aaa00003", title="Some news",
        summary="about AI",
    )
    prov = ScriptedProvider([
        json.dumps({"pillar": "ai", "score": 7.0, "reason": "ok"})
    ])
    set_provider(prov)

    scorer.score_topic(
        conn, topic, pillars=_pillars(), now="2026-07-05T01:00:00+00:00"
    )

    prompt = prov.calls[0]["prompt"]
    assert "Some news" in prompt
    assert "about AI" in prompt
    assert "ai" in prompt and "AI/科技" in prompt
    assert "oss" in prompt and "开源测评" in prompt
    assert "时效性" in prompt  # scoring_hint
    # JSON 输出约束
    assert "JSON" in prompt or "json" in prompt


# ── 解析失败重试 ─────────────────────────────────────

def test_parse_fail_retries_once_then_rejects(tmp_path) -> None:
    """LLM 返回非 JSON → 重试一次；仍失败 → 状态转 rejected。"""
    conn = _open_db(tmp_path)
    topic = _insert_raw(conn, id="t_aaa00004", title="bad json case")
    prov = ScriptedProvider(["not json at all", "still not json"])
    set_provider(prov)

    result = scorer.score_topic(
        conn, topic, pillars=_pillars(), now="2026-07-05T01:00:00+00:00"
    )

    assert result.accepted is False
    assert result.pillar is None
    assert result.score is None
    # 状态转 rejected
    row = conn.execute("SELECT * FROM topics WHERE id=?", ("t_aaa00004",)).fetchone()
    assert row["status"] == TopicStatus.REJECTED.value
    assert row["score"] is None
    # 调了 2 次（首次 + 重试）
    assert len(prov.calls) == 2


def test_parse_fail_then_success_on_retry(tmp_path) -> None:
    """LLM 首次坏 JSON → 重试 1 次成功。"""
    conn = _open_db(tmp_path)
    topic = _insert_raw(conn, id="t_aaa00005", title="flaky case")
    set_provider(ScriptedProvider([
        "garbage first",
        json.dumps({"pillar": "ai", "score": 6.5, "reason": "ok"}),
    ]))

    result = scorer.score_topic(
        conn, topic, pillars=_pillars(), now="2026-07-05T01:00:00+00:00"
    )

    assert result.accepted is True
    assert result.score == 6.5


# ── JSON 校验 ──────────────────────────────────────────

def test_pillar_must_match_known(tmp_path) -> None:
    """LLM 返回的 pillar 不在 config 里 → 视为解析失败，重试。"""
    conn = _open_db(tmp_path)
    topic = _insert_raw(conn, id="t_aaa00006", title="bogus pillar")
    set_provider(ScriptedProvider([
        json.dumps({"pillar": "bogus", "score": 7.0, "reason": "ok"}),
        json.dumps({"pillar": "ai", "score": 7.0, "reason": "ok"}),
    ]))

    result = scorer.score_topic(
        conn, topic, pillars=_pillars(), now="2026-07-05T01:00:00+00:00"
    )

    assert result.accepted is True
    assert result.pillar == "ai"


def test_score_out_of_range_rejected(tmp_path) -> None:
    """score 不在 [0, 10] 视为解析失败。"""
    conn = _open_db(tmp_path)
    topic = _insert_raw(conn, id="t_aaa00007", title="bad score")
    set_provider(ScriptedProvider([
        json.dumps({"pillar": "ai", "score": 99, "reason": "ok"}),
        json.dumps({"pillar": "ai", "score": 7.0, "reason": "ok"}),
    ]))

    result = scorer.score_topic(
        conn, topic, pillars=_pillars(), now="2026-07-05T01:00:00+00:00"
    )

    assert result.accepted is True
    assert result.score == 7.0