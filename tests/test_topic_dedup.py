"""AI 语义主题去重纯函数单元测试（M1-7）。

借鉴 Horizon src/orchestrator.py::merge_topic_duplicates + src/ai/prompts.py
TOPIC_DEDUP_SYSTEM/USER（MIT License，commit 3e21c04）的设计：

单次 LLM 调用让模型识别"同事件不同 URL/不同标题"的条目组；
返回的每组中第一个 idx 作代表（primary），其余作 duplicate。

失败语义（best-effort，不阻塞 score 主流程）：
  - LLM 异常 / JSON 解析失败 / 返回结构非法 → 返回 (items, [])
  - 测试覆盖以上各路异常
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from pipeline import db
from pipeline.creators import llm as llm_mod
from pipeline.creators.llm import (
    CompletionResult,
    LLMProvider,
    RetryableError,
    set_provider,
)
from pipeline.models import Topic, TopicStatus
from pipeline.sources.dedup import content_hash
from pipeline.topics.topic_dedup import dedup_topics


# ── helpers ────────────────────────────────────────────


class ScriptedProvider(LLMProvider):
    """按调用顺序返回不同 response 的脚本 provider。"""

    def __init__(self, responses: list[str], *, fail_all: bool = False):
        self._responses = list(responses)
        self._fail_all = fail_all
        self.calls: list[str] = []

    def call(self, prompt, model, max_tokens):
        self.calls.append(prompt)
        if self._fail_all:
            raise RetryableError("network fail (scripted)")
        if not self._responses:
            raise RetryableError("no scripted response")
        return CompletionResult(
            text=self._responses.pop(0),
            input_tokens=100, output_tokens=50,
        )


def _make_topic(*, id: str, title: str, url: str | None = None,
                summary: str | None = None, now: str = "2026-07-07T00:00:00+00:00"
                ) -> Topic:
    return Topic(
        id=id, source="rss:test", title=title, url=url, summary=summary,
        content_hash=content_hash(title, url),
        pillar=None, score=None, score_reason=None,
        status=TopicStatus.RAW.value,
        created_at=now, updated_at=now,
    )


@pytest.fixture
def db_conn(tmp_path: Path) -> sqlite3.Connection:
    """提供 DB 连接 + llm 模块级状态注入（与 canonical.py 集成一致）。"""
    p = tmp_path / "state.db"
    c = db.connect(p)
    db.init_db(c)
    llm_mod.init_db_conn(c)
    set_provider(ScriptedProvider([]))
    yield c
    c.close()
    llm_mod.init_db_conn(None)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _reset_provider():
    """每个 test 后重置 provider，避免污染。"""
    yield
    set_provider(ScriptedProvider([]))


# ── 边界 ──────────────────────────────────────────────────


def test_dedup_empty_list_returns_empty() -> None:
    """空列表 → 双空，不调 LLM。"""
    reps, dups = dedup_topics([])
    assert reps == []
    assert dups == []


def test_dedup_single_item_passthrough() -> None:
    """单条 → 原样返回（不调 LLM，1 条无分组意义）。"""
    t = _make_topic(id="t1", title="Only one")
    reps, dups = dedup_topics([t])
    assert reps == [t]
    assert dups == []


def test_dedup_no_llm_calls_when_empty_or_single() -> None:
    """空/单 → 不调 LLM（ScriptedProvider 没设 response，但不应被触发）。"""
    set_provider(ScriptedProvider([]))  # 触发会 RetryableError
    dedup_topics([])
    dedup_topics([_make_topic(id="t1", title="only")])
    # 调用记录应为空（0 次 LLM 调用）
    assert llm_mod._PROVIDER.calls == []  # type: ignore[attr-defined]


# ── 成功路径 ──────────────────────────────────────────────


def test_dedup_no_duplicates_returns_all(db_conn) -> None:
    """LLM 返回 {"duplicates": []} → 全部进 reps。"""
    items = [
        _make_topic(id="t1", title="AI news"),
        _make_topic(id="t2", title="Sports news"),
    ]
    set_provider(ScriptedProvider([
        json.dumps({"duplicates": []}),
    ]))

    reps, dups = dedup_topics(items)

    assert [r.id for r in reps] == ["t1", "t2"]
    assert dups == []


def test_dedup_one_duplicate_group_keeps_primary(db_conn) -> None:
    """LLM 识别 1 组 dup（idx 0 = primary, idx 1 = dup）→ reps=[items[0]], dups=[items[1]]。"""
    items = [
        _make_topic(id="t1", title="GPT-5 released today"),
        _make_topic(id="t2", title="OpenAI announces GPT-5 launch"),
        _make_topic(id="t3", title="Random unrelated"),
    ]
    set_provider(ScriptedProvider([
        json.dumps({"duplicates": [[0, 1]]}),
    ]))

    reps, dups = dedup_topics(items)

    assert [r.id for r in reps] == ["t1", "t3"]
    assert [d.id for d in dups] == ["t2"]


def test_dedup_multiple_duplicate_groups(db_conn) -> None:
    """LLM 识别 2 组 dup → reps=2（各组 primary）+dups=2。"""
    items = [
        _make_topic(id="t1", title="Event A main"),
        _make_topic(id="t2", title="Event A duplicate 1"),
        _make_topic(id="t3", title="Event A duplicate 2"),
        _make_topic(id="t4", title="Event B main"),
        _make_topic(id="t5", title="Event B duplicate"),
    ]
    set_provider(ScriptedProvider([
        json.dumps({"duplicates": [[0, 1, 2], [3, 4]]}),
    ]))

    reps, dups = dedup_topics(items)

    assert [r.id for r in reps] == ["t1", "t4"]
    assert {d.id for d in dups} == {"t2", "t3", "t5"}


def test_dedup_preserves_input_order_in_reps(db_conn) -> None:
    """representatives 保留输入顺序（按 idx 升序遍历）。"""
    items = [
        _make_topic(id="t1", title="A"),
        _make_topic(id="t2", title="A dup"),
        _make_topic(id="t3", title="B"),
    ]
    set_provider(ScriptedProvider([
        json.dumps({"duplicates": [[0, 1]]}),
    ]))

    reps, dups = dedup_topics(items)

    assert [r.id for r in reps] == ["t1", "t3"]
    assert [d.id for d in dups] == ["t2"]


def test_dedup_prompt_contains_all_titles(db_conn) -> None:
    """prompt 的 items 段含所有 topic 的 title（LLM 必须能看见所有条目才能分组）。"""
    items = [
        _make_topic(id="t1", title="ALPHA_news"),
        _make_topic(id="t2", title="BETA_news"),
        _make_topic(id="t3", title="GAMMA_news"),
    ]
    set_provider(ScriptedProvider([
        json.dumps({"duplicates": []}),
    ]))

    dedup_topics(items)

    prompt = llm_mod._PROVIDER.calls[0]  # type: ignore[attr-defined]
    assert "ALPHA_news" in prompt
    assert "BETA_news" in prompt
    assert "GAMMA_news" in prompt
    # items 段用 [idx] 前缀索引
    assert "[0]" in prompt
    assert "[2]" in prompt


# ── 失败 fallback（静默返回 items） ──────────────────────────


def test_dedup_invalid_json_falls_back(db_conn) -> None:
    """LLM 返回非 JSON → complete_json 重试 1 次还是坏 → fallback (items, [])。"""
    items = [_make_topic(id="t1", title="A"), _make_topic(id="t2", title="B")]
    set_provider(ScriptedProvider([
        "not json {",
        "still not json",
    ]))

    reps, dups = dedup_topics(items)

    # fallback：全部进 reps，无 dups
    assert [r.id for r in reps] == ["t1", "t2"]
    assert dups == []


def test_dedup_json_not_dict_falls_back(db_conn) -> None:
    """JSON 是 list/str 而非 dict → 解析视为失败 → fallback。"""
    items = [_make_topic(id="t1", title="A"), _make_topic(id="t2", title="B")]
    set_provider(ScriptedProvider([
        json.dumps([1, 2, 3]),  # 不是 dict
        json.dumps({"duplicates": []}),  # 第二次重试时给合规的，避免 retry 用尽
    ]))
    # 注意：上面的 retry 实际会让第二次尝试成功；但我们要测"持续失败"的语义，
    # 所以换一个：两次都坏
    set_provider(ScriptedProvider([
        json.dumps([1, 2, 3]),
        json.dumps("not a dict"),
    ]))

    reps, dups = dedup_topics(items)

    assert [r.id for r in reps] == ["t1", "t2"]
    assert dups == []


def test_dedup_missing_duplicates_key_falls_back(db_conn) -> None:
    """JSON 是 dict 但缺 'duplicates' key → fallback。"""
    items = [_make_topic(id="t1", title="A"), _make_topic(id="t2", title="B")]
    set_provider(ScriptedProvider([
        json.dumps({"groups": []}),
        json.dumps({"groups": []}),
    ]))

    reps, dups = dedup_topics(items)

    assert [r.id for r in reps] == ["t1", "t2"]
    assert dups == []


def test_dedup_duplicates_not_list_falls_back(db_conn) -> None:
    """JSON 是 dict 但 'duplicates' 不是 list → fallback。"""
    items = [_make_topic(id="t1", title="A"), _make_topic(id="t2", title="B")]
    set_provider(ScriptedProvider([
        json.dumps({"duplicates": "not a list"}),
        json.dumps({"duplicates": "not a list"}),
    ]))

    reps, dups = dedup_topics(items)

    assert [r.id for r in reps] == ["t1", "t2"]
    assert dups == []


def test_dedup_llm_retryable_error_falls_back(db_conn) -> None:
    """LLM 持续 RetryableError（网络瞬时）→ fallback（best-effort 不阻塞）。"""
    items = [_make_topic(id="t1", title="A"), _make_topic(id="t2", title="B")]
    set_provider(ScriptedProvider([], fail_all=True))

    reps, dups = dedup_topics(items)

    assert [r.id for r in reps] == ["t1", "t2"]
    assert dups == []


def test_dedup_single_element_group_is_ignored(db_conn) -> None:
    """组只有 1 个 idx（primary = dup）→ 跳过该组，无 dups。"""
    items = [
        _make_topic(id="t1", title="A"),
        _make_topic(id="t2", title="B"),
    ]
    set_provider(ScriptedProvider([
        json.dumps({"duplicates": [[0]]}),  # 单元素组，无意义
        json.dumps({"duplicates": [[0]]}),
    ]))

    reps, dups = dedup_topics(items)

    assert [r.id for r in reps] == ["t1", "t2"]
    assert dups == []


def test_dedup_out_of_range_indices_are_skipped(db_conn) -> None:
    """组中 idx 越界或非 int → 跳过该组（保守）。"""
    items = [
        _make_topic(id="t1", title="A"),
        _make_topic(id="t2", title="B"),
    ]
    set_provider(ScriptedProvider([
        json.dumps({"duplicates": [[0, 99]]}),  # 99 越界
        json.dumps({"duplicates": [[0, 99]]}),
    ]))

    reps, dups = dedup_topics(items)

    assert [r.id for r in reps] == ["t1", "t2"]
    assert dups == []


# ── parse 函数独立测试 ────────────────────────────────────


def test_parse_response_accepts_valid_dict() -> None:
    """parse 回调：合法 dict → 原样返回。"""
    from pipeline.topics.topic_dedup import _parse_response
    obj = _parse_response(json.dumps({"duplicates": [[0, 1]]}))
    assert obj == {"duplicates": [[0, 1]]}


def test_parse_response_accepts_empty_duplicates() -> None:
    """parse 回调：空 duplicates → 原样返回。"""
    from pipeline.topics.topic_dedup import _parse_response
    obj = _parse_response(json.dumps({"duplicates": []}))
    assert obj == {"duplicates": []}


def test_parse_response_rejects_non_dict() -> None:
    """parse 回调：list → 抛 ValueError（让 complete_json 重试）。"""
    from pipeline.topics.topic_dedup import _parse_response
    with pytest.raises(ValueError):
        _parse_response(json.dumps([1, 2, 3]))


def test_parse_response_rejects_missing_key() -> None:
    """parse 回调：缺 'duplicates' → 抛 ValueError。"""
    from pipeline.topics.topic_dedup import _parse_response
    with pytest.raises(ValueError):
        _parse_response(json.dumps({"groups": []}))


def test_parse_response_rejects_invalid_json() -> None:
    """parse 回调：非 JSON → 抛 JSONDecodeError。"""
    from pipeline.topics.topic_dedup import _parse_response
    with pytest.raises(json.JSONDecodeError):
        _parse_response("not json {")
