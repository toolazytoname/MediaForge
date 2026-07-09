"""M10-2 db_reads.py 单元测试。

覆盖 6 个只读查询函数：
  - row_to_metric
  - get_latest_metric / get_metrics_series
  - llm_cost_by_stage / llm_cost_by_day
  - platform_metric_totals
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from pipeline import db, db_reads
from pipeline.models import (
    Content, ContentStatus,
    Metric,
    Publication, PublicationStatus,
    Topic, TopicStatus,
)
from pipeline.utils.ids import new_id


# ── Fixtures ────────────────────────────────────────────────


@pytest.fixture
def conn(tmp_path):
    p = tmp_path / "test.db"
    c = db.connect(p)
    db.init_db(c)
    yield c
    c.close()


def _topic(**kw) -> Topic:
    base = dict(
        id=new_id("t"), source="rss:test", title="T",
        url=None, summary=None, content_hash="h_default",
        pillar=None, score=None, score_reason=None,
        status=TopicStatus.RAW,
        created_at="2026-07-05T00:00:00+00:00",
        updated_at="2026-07-05T00:00:00+00:00",
    )
    base.update(kw)
    return Topic(**base)


def _content(topic_id: str, **kw) -> Content:
    base = dict(
        id=new_id("c"), topic_id=topic_id, pillar="ai_daily",
        title="C", canonical_path="output/x/canonical.md",
        formats=("x",), gate_score_total=None, gate_scores=None,
        gate_verdict=None, status=ContentStatus.DRAFT,
        created_at="2026-07-05T00:00:00+00:00",
        updated_at="2026-07-05T00:00:00+00:00",
    )
    base.update(kw)
    return Content(**base)


def _pub(content_id: str, **kw) -> Publication:
    base = dict(
        id=new_id("p"), content_id=content_id, platform="x",
        account_id="main",
        scheduled_at="2026-07-05T10:00:00+00:00",
        published_at=None, platform_post_id=None, platform_url=None,
        error=None, retry_count=0, status=PublicationStatus.QUEUED,
        created_at="2026-07-05T00:00:00+00:00",
        updated_at="2026-07-05T00:00:00+00:00",
    )
    base.update(kw)
    return Publication(**base)


def _metric(pub_id: str, **kw) -> Metric:
    base = dict(
        publication_id=pub_id,
        collected_at="2026-07-05T12:00:00+00:00",
        views=100, likes=10, comments=2, shares=1,
        followers_delta=0, raw=None,
    )
    base.update(kw)
    return Metric(**base)


def _seed_published(conn, *, platform="x", account_id="main",
                    views=100, likes=10, comments=2, shares=1):
    """造一条 published 状态 publication + 一条 metric（用于 platform_metric_totals）。"""
    t = _topic(content_hash=f"h_{platform}_{account_id}_{views}")
    db.insert_topic(conn, t)
    c = _content(t.id)
    db.insert_content(conn, c)
    p = _pub(c.id, platform=platform, account_id=account_id)
    db.insert_publication(conn, p)
    # queued → publishing → published
    db.transition(conn, "publications", p.id,
                  PublicationStatus.QUEUED, PublicationStatus.PUBLISHING)
    db.transition(conn, "publications", p.id,
                  PublicationStatus.PUBLISHING, PublicationStatus.PUBLISHED)
    db.insert_metric(conn, _metric(p.id,
                                   views=views, likes=likes,
                                   comments=comments, shares=shares))
    return p


# ── row_to_metric ──────────────────────────────────────────


class TestRowToMetric:
    def test_basic_mapping(self, conn):
        # 走真实 helpers 造 row（FK 链完整）
        t = _topic(content_hash="h_rtm")
        db.insert_topic(conn, t)
        c = _content(t.id)
        db.insert_content(conn, c)
        p = _pub(c.id)
        db.insert_publication(conn, p)
        db.insert_metric(conn, _metric(p.id, views=200, likes=20,
                                       comments=5, shares=1, raw='{"x":1}'))
        row = conn.execute(
            "SELECT * FROM metrics WHERE publication_id=?", (p.id,),
        ).fetchone()
        m = db_reads.row_to_metric(row)
        assert isinstance(m, Metric)
        assert m.publication_id == p.id
        assert m.views == 200
        assert m.raw == '{"x":1}'


# ── get_latest_metric / get_metrics_series ──────────────────


class TestGetLatestMetric:
    def test_returns_none_when_no_metric(self, conn):
        assert db_reads.get_latest_metric(conn, "p_nope") is None

    def test_returns_most_recent(self, conn):
        t = _topic(content_hash="h_glm")
        db.insert_topic(conn, t)
        c = _content(t.id)
        db.insert_content(conn, c)
        p = _pub(c.id)
        db.insert_publication(conn, p)
        db.insert_metric(conn, _metric(p.id, collected_at="2026-07-05T10:00:00+00:00",
                                       views=10))
        db.insert_metric(conn, _metric(p.id, collected_at="2026-07-06T10:00:00+00:00",
                                       views=99))
        m = db_reads.get_latest_metric(conn, p.id)
        assert m.views == 99
        assert m.collected_at == "2026-07-06T10:00:00+00:00"


class TestGetMetricsSeries:
    def test_empty(self, conn):
        assert db_reads.get_metrics_series(conn, "p_nope") == []

    def test_returns_sorted_asc(self, conn):
        t = _topic(content_hash="h_gms")
        db.insert_topic(conn, t)
        c = _content(t.id)
        db.insert_content(conn, c)
        p = _pub(c.id)
        db.insert_publication(conn, p)
        db.insert_metric(conn, _metric(p.id, collected_at="2026-07-07T10:00:00+00:00",
                                       views=30))
        db.insert_metric(conn, _metric(p.id, collected_at="2026-07-05T10:00:00+00:00",
                                       views=10))
        series = db_reads.get_metrics_series(conn, p.id)
        assert [m.views for m in series] == [10, 30]


# ── llm_cost_by_stage ──────────────────────────────────────


class TestLlmCostByStage:
    def test_empty(self, conn):
        assert db_reads.llm_cost_by_stage(conn) == []

    def test_groups_by_stage(self, conn):
        db.insert_llm_call(conn, stage="score", model="m",
                           input_tokens=100, output_tokens=50,
                           cost_usd=0.001, created_at="2026-07-05T10:00:00+00:00")
        db.insert_llm_call(conn, stage="score", model="m",
                           input_tokens=200, output_tokens=80,
                           cost_usd=0.002, created_at="2026-07-05T11:00:00+00:00")
        db.insert_llm_call(conn, stage="create", model="m",
                           input_tokens=500, output_tokens=300,
                           cost_usd=0.020, created_at="2026-07-05T12:00:00+00:00")
        out = db_reads.llm_cost_by_stage(conn)
        # ORDER BY cost_usd DESC
        assert out[0]["stage"] == "create"
        assert out[0]["cost_usd"] == pytest.approx(0.020)
        assert out[0]["calls"] == 1
        assert out[1]["stage"] == "score"
        assert out[1]["cost_usd"] == pytest.approx(0.003)
        assert out[1]["calls"] == 2
        assert out[1]["input_tokens"] == 300
        assert out[1]["output_tokens"] == 130

    def test_since_until_filter(self, conn):
        db.insert_llm_call(conn, stage="score", model="m", cost_usd=0.001,
                           created_at="2026-07-01T10:00:00+00:00")
        db.insert_llm_call(conn, stage="score", model="m", cost_usd=0.005,
                           created_at="2026-07-10T10:00:00+00:00")
        out = db_reads.llm_cost_by_stage(conn,
                                         since_iso="2026-07-05T00:00:00+00:00",
                                         until_iso="2026-07-15T00:00:00+00:00")
        assert len(out) == 1
        assert out[0]["cost_usd"] == pytest.approx(0.005)


# ── llm_cost_by_day ────────────────────────────────────────


class TestLlmCostByDay:
    def test_empty(self, conn):
        assert db_reads.llm_cost_by_day(conn) == []

    def test_groups_by_date(self, conn):
        db.insert_llm_call(conn, stage="score", model="m", cost_usd=0.001,
                           created_at="2026-07-08T10:00:00+00:00")
        db.insert_llm_call(conn, stage="score", model="m", cost_usd=0.002,
                           created_at="2026-07-08T22:00:00+00:00")
        db.insert_llm_call(conn, stage="create", model="m", cost_usd=0.020,
                           created_at="2026-07-09T03:00:00+00:00")
        now = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)
        out = db_reads.llm_cost_by_day(conn, days=30, now=now)
        assert len(out) == 2
        assert out[0] == {"date": "2026-07-08", "calls": 2, "cost_usd": pytest.approx(0.003)}
        assert out[1] == {"date": "2026-07-09", "calls": 1, "cost_usd": pytest.approx(0.020)}

    def test_caps_to_days(self, conn):
        now = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)
        # 注入一条早于 now-days 的记录（since_iso 已排除）
        db.insert_llm_call(conn, stage="old", model="m", cost_usd=0.999,
                           created_at="2025-01-01T00:00:00+00:00")
        out = db_reads.llm_cost_by_day(conn, days=30, now=now)
        assert out == []


# ── platform_metric_totals ──────────────────────────────────


class TestPlatformMetricTotals:
    def test_empty(self, conn):
        assert db_reads.platform_metric_totals(conn) == []

    def test_aggregates_by_platform(self, conn):
        _seed_published(conn, platform="x", account_id="a1", views=100, likes=5)
        _seed_published(conn, platform="x", account_id="a2", views=200, likes=15)
        _seed_published(conn, platform="toutiao", account_id="a1", views=50, likes=2)
        out = db_reads.platform_metric_totals(conn)
        # x 有 2 条 publications，sort desc
        assert out[0]["platform"] == "x"
        assert out[0]["publications"] == 2
        assert out[0]["latest_views"] == 300
        assert out[0]["latest_likes"] == 20
        # toutiao 1 条
        assert out[1]["platform"] == "toutiao"
        assert out[1]["publications"] == 1
        assert out[1]["latest_views"] == 50

    def test_excludes_non_published(self, conn):
        # queued 不计入（看 status filter）
        t = _topic(content_hash="h_q")
        db.insert_topic(conn, t)
        c = _content(t.id)
        db.insert_content(conn, c)
        db.insert_publication(conn, _pub(c.id, account_id="a1"))  # queued
        out = db_reads.platform_metric_totals(conn)
        assert out == []

    def test_publication_without_metric_returns_zeros(self, conn):
        # published 但还没 collect 过的也计入，metrics 字段为 0
        t = _topic(content_hash="h_p_no_m")
        db.insert_topic(conn, t)
        c = _content(t.id)
        db.insert_content(conn, c)
        p = _pub(c.id, account_id="a1")
        db.insert_publication(conn, p)
        db.transition(conn, "publications", p.id,
                      PublicationStatus.QUEUED, PublicationStatus.PUBLISHING)
        db.transition(conn, "publications", p.id,
                      PublicationStatus.PUBLISHING, PublicationStatus.PUBLISHED)
        out = db_reads.platform_metric_totals(conn)
        assert out[0]["publications"] == 1
        assert out[0]["latest_views"] == 0
