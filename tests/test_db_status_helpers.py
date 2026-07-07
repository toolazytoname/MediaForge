"""M8 S8-1 辅助测试：db.count_by_status 与 db.sum_llm_cost_this_month。

轻量回归 + 直接单元测试（不走 cmd_status CLI 层）。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pipeline import db


@pytest.fixture
def tmp_db(tmp_path: Path) -> sqlite3.Connection:
    c = db.connect(tmp_path / "state.db")
    db.init_db(c)
    return c


# ── count_by_status ──────────────────────────────────────


class TestCountByStatus:
    def test_empty_table_returns_empty_dict(self, tmp_db) -> None:
        assert db.count_by_status(tmp_db, "topics") == {}

    def test_groups_by_status(self, tmp_db) -> None:
        from pipeline.models import Topic, TopicStatus
        from pipeline.sources.dedup import content_hash

        now = "2026-07-07T00:00:00+00:00"
        seeds = [
            ("t_aaaaaaaa", "raw"),
            ("t_bbbbbbbb", "raw"),
            ("t_cccccccc", "scored"),
            ("t_dddddddd", "rejected"),
        ]
        for tid, st in seeds:
            db.insert_topic(tmp_db, Topic(
                id=tid, source="rss:test", title=f"T-{tid}",
                url=None, summary=None,
                content_hash=content_hash(f"T-{tid}", None),
                pillar=None, score=None, score_reason=None,
                status=st, created_at=now, updated_at=now,
            ))

        result = db.count_by_status(tmp_db, "topics")
        assert result == {"raw": 2, "scored": 1, "rejected": 1}

    def test_rejects_unknown_table(self, tmp_db) -> None:
        with pytest.raises(ValueError, match=r"contents.*publications.*topics"):
            db.count_by_status(tmp_db, "metrics")


# ── sum_llm_cost_this_month ──────────────────────────────


class TestSumLlmCostThisMonth:
    def test_empty_returns_zero(self, tmp_db) -> None:
        now = datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc)
        assert db.sum_llm_cost_this_month(tmp_db, now=now) == 0.0

    def test_sums_with_injected_now(self, tmp_db) -> None:
        """注入 now=2026-07 → 只合计 2026-07-xxx；2020-xx 排除。"""
        tmp_db.execute(
            """
            INSERT INTO llm_calls
                (stage, ref_id, model, input_tokens, output_tokens,
                 cost_usd, created_at)
            VALUES ('score', NULL, 'm', 1, 1, 0.123, ?),
                    ('score', NULL, 'm', 1, 1, 0.456, ?),
                    ('old',   NULL, 'm', 1, 1, 99.0, ?)
            """,
            (
                "2026-07-15T10:00:00+00:00",
                "2026-07-01T10:00:00+00:00",
                "2020-01-01T10:00:00+00:00",
            ),
        )
        tmp_db.commit()

        now = datetime(2026, 7, 7, tzinfo=timezone.utc)
        # 0.123 + 0.456 = 0.579
        assert db.sum_llm_cost_this_month(tmp_db, now=now) == pytest.approx(0.579)

    def test_different_month_no_sum(self, tmp_db) -> None:
        """now=2026-07 但 inserted_at 全在 2026-08 → 0.0。"""
        tmp_db.execute(
            """
            INSERT INTO llm_calls
                (stage, ref_id, model, input_tokens, output_tokens,
                 cost_usd, created_at)
            VALUES ('x', NULL, 'm', 1, 1, 1.0, '2026-08-15T00:00:00+00:00')
            """
        )
        tmp_db.commit()

        now = datetime(2026, 7, 7, tzinfo=timezone.utc)
        assert db.sum_llm_cost_this_month(tmp_db, now=now) == 0.0
