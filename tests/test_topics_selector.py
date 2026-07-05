"""topics/selector.py 单元测试（M1-4）。

行为契约：
  - 输入 scored topics + quota + min_score
  - 按 score desc 排序，取 score ≥ min_score 的前 quota 个 → status=selected
  - 余下 scored 不动（保留到明天，3 天后由过期任务转 rejected——非本任务范围）
  - 已 selected / rejected / consumed 的不进 select
  - 同一 topic 不会重复晋升（状态机保证）
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pipeline import db
from pipeline.models import Topic, TopicStatus
from pipeline.sources.dedup import content_hash
from pipeline.topics import selector


def _open_db(tmp_path: Path) -> sqlite3.Connection:
    p = tmp_path / "state.db"
    c = db.connect(p)
    db.init_db(c)
    return c


def _seed_scored(
    conn, *, scores: list[float], statuses: list[str] | None = None,
) -> list[Topic]:
    """插入 scored topic 列表（statuses 默认全 scored）。"""
    from pipeline.utils.ids import new_id

    topics = []
    statuses = statuses or [TopicStatus.SCORED.value] * len(scores)
    for i, (s, st) in enumerate(zip(scores, statuses)):
        t = Topic(
            id=new_id("t"),
            source="rss:test",
            title=f"topic {i} (score={s})",
            url=None, summary=None,
            content_hash=content_hash(f"topic {i} (score={s})", None),
            pillar="ai", score=s, score_reason="ok",
            status=st,
            created_at="2026-07-05T00:00:00+00:00",
            updated_at="2026-07-05T01:00:00+00:00",
        )
        db.insert_topic(conn, t)
        topics.append(t)
    return topics


# ── 正常路径 ──────────────────────────────────────────

def test_select_top_n_by_score(tmp_path) -> None:
    """按 score desc 取 top N。"""
    conn = _open_db(tmp_path)
    _seed_scored(conn, scores=[5.0, 9.0, 7.0, 8.0])

    result = selector.select_daily(
        conn, quota=2, min_score=6.0, now="2026-07-05T02:00:00+00:00"
    )

    assert len(result.selected) == 2
    assert [t.score for t in result.selected] == [9.0, 8.0]


def test_respects_min_score(tmp_path) -> None:
    """score < min_score 的不进 selected（仍保持 scored）。"""
    conn = _open_db(tmp_path)
    _seed_scored(conn, scores=[9.0, 3.0, 7.0, 5.0])

    result = selector.select_daily(
        conn, quota=5, min_score=6.0, now="2026-07-05T02:00:00+00:00"
    )

    assert len(result.selected) == 2
    assert sorted(t.score for t in result.selected) == [7.0, 9.0]
    # 状态：score<6 的两条仍 scored，>6 的两条 selected
    rows = conn.execute(
        "SELECT score, status FROM topics ORDER BY score"
    ).fetchall()
    assert rows[0]["status"] == TopicStatus.SCORED.value  # 3.0
    assert rows[1]["status"] == TopicStatus.SCORED.value  # 5.0
    assert rows[2]["status"] == TopicStatus.SELECTED.value  # 7.0
    assert rows[3]["status"] == TopicStatus.SELECTED.value  # 9.0


def test_quota_limits_selection(tmp_path) -> None:
    """quota 是硬上限，即使 score 全 ≥ min_score 也只取 quota 个。"""
    conn = _open_db(tmp_path)
    _seed_scored(conn, scores=[10.0, 9.0, 8.0, 7.0, 6.0])

    result = selector.select_daily(
        conn, quota=3, min_score=5.0, now="2026-07-05T02:00:00+00:00"
    )

    assert len(result.selected) == 3
    assert [t.score for t in result.selected] == [10.0, 9.0, 8.0]


# ── 边界 ────────────────────────────────────────────

def test_no_scored_topics_is_noop(tmp_path) -> None:
    """无 scored topic → 0/0。"""
    conn = _open_db(tmp_path)

    result = selector.select_daily(
        conn, quota=5, min_score=6.0, now="2026-07-05T02:00:00+00:00"
    )

    assert result.selected == ()
    assert result.kept_scored == 0


def test_only_below_min_score_noop(tmp_path) -> None:
    """全 < min_score → 全保持 scored，selected=0。"""
    conn = _open_db(tmp_path)
    _seed_scored(conn, scores=[3.0, 4.0, 5.0])

    result = selector.select_daily(
        conn, quota=5, min_score=6.0, now="2026-07-05T02:00:00+00:00"
    )

    assert result.selected == ()
    assert result.kept_scored == 3


def test_already_selected_topics_not_re_scored(tmp_path) -> None:
    """已 selected 的不进 select（select_daily 只看 scored）。"""
    conn = _open_db(tmp_path)
    _seed_scored(
        conn,
        scores=[9.0, 5.0],
        statuses=[TopicStatus.SCORED.value, TopicStatus.SELECTED.value],
    )

    result = selector.select_daily(
        conn, quota=5, min_score=6.0, now="2026-07-05T02:00:00+00:00"
    )

    assert len(result.selected) == 1
    assert result.selected[0].score == 9.0


# ── 幂等 ────────────────────────────────────────────

def test_run_twice_second_is_noop(tmp_path) -> None:
    """二次运行：已 selected 的不再选（HARD_PARTS §5）。"""
    conn = _open_db(tmp_path)
    _seed_scored(conn, scores=[9.0, 8.0, 7.0])

    first = selector.select_daily(
        conn, quota=5, min_score=6.0, now="2026-07-05T02:00:00+00:00"
    )
    second = selector.select_daily(
        conn, quota=5, min_score=6.0, now="2026-07-05T03:00:00+00:00"
    )

    assert len(first.selected) == 3
    assert second.selected == ()  # 全已 selected


# ── 状态机保证 ──────────────────────────────────────

def test_transition_uses_status_machine(tmp_path) -> None:
    """select 走 db.transition()，scored→selected 是合法转移。"""
    conn = _open_db(tmp_path)
    _seed_scored(conn, scores=[9.0])

    selector.select_daily(
        conn, quota=5, min_score=6.0, now="2026-07-05T02:00:00+00:00"
    )

    row = conn.execute("SELECT status FROM topics").fetchone()
    assert row["status"] == TopicStatus.SELECTED.value