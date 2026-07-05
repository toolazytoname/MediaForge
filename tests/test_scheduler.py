"""M3-1 scheduler：错峰排期。

测试 plan() 纯函数：黄金时段取点 / 整点规避 / 同平台间隔 / 跨平台错开 / 顺延次日 / 幂等。
参考：ARCHITECTURE §3.6、HARD_PARTS §8、TASKS M3-1。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pipeline import db
from pipeline.config import PlatformAPI, PlatformPlaywright
from pipeline.models import (
    Content,
    ContentStatus,
    Publication,
    PublicationStatus,
)
from pipeline.scheduler import (
    PlanResult,
    Window,
    _parse_window,
    plan,
)


# ── helpers ────────────────────────────────────────────────

_TZ = ZoneInfo("Asia/Shanghai")
_UTC = timezone.utc
_NOW_LOCAL = datetime(2026, 7, 5, 10, 0, tzinfo=_TZ)  # 10:00 北京
_NOW_UTC = _NOW_LOCAL.astimezone(_UTC)
_NOW_ISO = _NOW_UTC.isoformat()


def _conn(tmp_path: Path) -> sqlite3.Connection:
    c = db.connect(tmp_path / "state.db")
    db.init_db(c)
    return c


def _make_content(
    conn: sqlite3.Connection, *, id: str = "c_appv0001",
    pillar: str = "ai_daily", title: str = "测试",
) -> Content:
    """approved 内容，UNIQUE(topic_id) 需要 topic_id 唯一。"""
    from pipeline.models import Topic, TopicStatus
    now = _NOW_ISO
    t = Topic(
        id="t_" + id.removeprefix("c_"), source="rss:test", title="T:" + id,
        url=None, summary=None, content_hash="h-" + id,
        pillar=pillar, score=None, score_reason=None,
        status=TopicStatus.CONSUMED.value, created_at=now, updated_at=now,
    )
    db.insert_topic(conn, t)
    c = Content(
        id=id, topic_id=t.id, pillar=pillar, title=title,
        canonical_path=f"output/2026-07-05/{id}/canonical.md",
        formats='["toutiao","xiaohongshu","x"]',
        gate_score_total=27.0,
        gate_scores='{"info":9,"fun":9,"view":9}',
        gate_verdict="通过", status=ContentStatus.APPROVED.value,
        created_at=now, updated_at=now,
    )
    db.insert_content(conn, c)
    return c


def _x_platform() -> PlatformAPI:
    """X 平台：09:00-11:00 + 21:00-23:00 北京。"""
    return PlatformAPI(
        kind="api",
        windows=["09:00-11:00", "21:00-23:00"],
        accounts=[],
    )


def _toutiao_platform() -> PlatformPlaywright:
    """头条：07:00-09:00 + 18:00-20:00 北京。"""
    return PlatformPlaywright(
        kind="playwright",
        windows=["07:00-09:00", "18:00-20:00"],
        accounts=[],
    )


def _xhs_platform() -> PlatformPlaywright:
    """小红书：12:00-14:00 + 19:00-22:00 北京。"""
    return PlatformPlaywright(
        kind="playwright",
        windows=["12:00-14:00", "19:00-22:00"],
        accounts=[],
    )


# ── _parse_window ──────────────────────────────────────────


class TestParseWindow:
    def test_basic(self) -> None:
        w = _parse_window("09:00-11:00")
        assert w.start_h == 9 and w.start_m == 0
        assert w.end_h == 11 and w.end_m == 0

    def test_with_minutes(self) -> None:
        w = _parse_window("07:30-09:15")
        assert w.start_h == 7 and w.start_m == 30
        assert w.end_h == 9 and w.end_m == 15

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            _parse_window("not-a-window")
        with pytest.raises(ValueError):
            _parse_window("25:00-26:00")


# ── plan() ─────────────────────────────────────────────────


class TestPlanBasic:
    def test_one_content_one_platform(
        self, tmp_path: Path
    ) -> None:
        conn = _conn(tmp_path)
        c = _make_content(conn)
        result = plan(
            [c], {"x": _x_platform()}, existing_publications=[],
            now_iso=_NOW_ISO, min_gap_hours=4,
            cross_platform_gap_minutes=30,
            tz_name="Asia/Shanghai",
        )
        assert len(result.publications) == 1
        pub = result.publications[0]
        assert pub.content_id == c.id
        assert pub.platform == "x"
        assert pub.status == PublicationStatus.QUEUED.value

    def test_three_platforms_for_one_content(
        self, tmp_path: Path
    ) -> None:
        """一条 approved 内容 × 3 平台 → 3 条 publication。"""
        conn = _conn(tmp_path)
        c = _make_content(conn)
        result = plan(
            [c],
            {"x": _x_platform(), "toutiao": _toutiao_platform(),
             "xiaohongshu": _xhs_platform()},
            existing_publications=[],
            now_iso=_NOW_ISO, min_gap_hours=4,
            cross_platform_gap_minutes=30,
            tz_name="Asia/Shanghai",
        )
        assert len(result.publications) == 3
        platforms = {p.platform for p in result.publications}
        assert platforms == {"x", "toutiao", "xiaohongshu"}

    def test_multiple_contents_all_scheduled(
        self, tmp_path: Path
    ) -> None:
        conn = _conn(tmp_path)
        c1 = _make_content(conn, id="c_appv0001")
        c2 = _make_content(conn, id="c_appv0002")
        c3 = _make_content(conn, id="c_appv0003")
        result = plan(
            [c1, c2, c3],
            {"x": _x_platform()},
            existing_publications=[],
            now_iso=_NOW_ISO, min_gap_hours=4,
            cross_platform_gap_minutes=30,
            tz_name="Asia/Shanghai",
        )
        assert len(result.publications) == 3
        # 不同 content 同 x 平台间隔 ≥ 4h
        pub1 = next(p for p in result.publications if p.content_id == c1.id)
        pub2 = next(p for p in result.publications if p.content_id == c2.id)
        pub3 = next(p for p in result.publications if p.content_id == c3.id)
        from pipeline.scheduler import _parse_iso_utc
        t1 = _parse_iso_utc(pub1.scheduled_at)
        t2 = _parse_iso_utc(pub2.scheduled_at)
        t3 = _parse_iso_utc(pub3.scheduled_at)
        times = sorted([t1, t2, t3])
        gaps = [
            (times[1] - times[0]).total_seconds() / 3600,
            (times[2] - times[1]).total_seconds() / 3600,
        ]
        # 至少第一个间隔 ≥ 4h
        assert gaps[0] >= 4.0


class TestPlanGoldenWindow:
    def test_scheduled_at_in_window(
        self, tmp_path: Path
    ) -> None:
        """排期时间必须落在黄金时段窗口内（本地时区）。"""
        conn = _conn(tmp_path)
        c = _make_content(conn)
        result = plan(
            [c], {"toutiao": _toutiao_platform()},
            existing_publications=[],
            now_iso=_NOW_ISO, min_gap_hours=4,
            cross_platform_gap_minutes=30,
            tz_name="Asia/Shanghai",
        )
        pub = result.publications[0]
        # 转本地时间
        t_utc = datetime.fromisoformat(pub.scheduled_at)
        t_local = t_utc.astimezone(_TZ)
        # 07:00-09:00 或 18:00-20:00
        in_morning = (
            t_local.hour >= 7 and t_local.hour < 9
        ) or (
            t_local.hour == 9 and t_local.minute == 0
        )
        in_evening = (
            t_local.hour >= 18 and t_local.hour < 20
        ) or (
            t_local.hour == 20 and t_local.minute == 0
        )
        assert in_morning or in_evening, f"local time {t_local} not in window"

    def test_avoids_top_of_hour(
        self, tmp_path: Path
    ) -> None:
        """排期时间不在整点 ±3min（HARD_PARTS §8）。"""
        conn = _conn(tmp_path)
        c = _make_content(conn)
        result = plan(
            [c], {"toutiao": _toutiao_platform()},
            existing_publications=[],
            now_iso=_NOW_ISO, min_gap_hours=4,
            cross_platform_gap_minutes=30,
            tz_name="Asia/Shanghai",
        )
        pub = result.publications[0]
        t_utc = datetime.fromisoformat(pub.scheduled_at)
        t_local = t_utc.astimezone(_TZ)
        # 避开整点：minute 不在 [0,3] 且不在 [57,59]
        assert not (
            (t_local.minute <= 3)
            or (t_local.minute >= 57)
        ), f"local time {t_local} too close to top of hour"


class TestPlanCrossPlatformGap:
    def test_cross_platform_gap_30min(
        self, tmp_path: Path
    ) -> None:
        """同一内容跨平台错开 ≥ 30min。"""
        conn = _conn(tmp_path)
        c = _make_content(conn)
        result = plan(
            [c],
            {"toutiao": _toutiao_platform(),
             "xiaohongshu": _xhs_platform()},
            existing_publications=[],
            now_iso=_NOW_ISO, min_gap_hours=4,
            cross_platform_gap_minutes=30,
            tz_name="Asia/Shanghai",
        )
        # 找 toutiao 和 xhs 两个时间
        from pipeline.scheduler import _parse_iso_utc
        toutiao = next(
            p for p in result.publications if p.platform == "toutiao"
        )
        xhs = next(
            p for p in result.publications if p.platform == "xiaohongshu"
        )
        t1 = _parse_iso_utc(toutiao.scheduled_at)
        t2 = _parse_iso_utc(xhs.scheduled_at)
        gap_min = abs((t2 - t1).total_seconds() / 60)
        # 至少 30min 间隔
        assert gap_min >= 30.0


class TestPlanSpillover:
    def test_spillover_to_next_day(
        self, tmp_path: Path
    ) -> None:
        """当日窗口排满 → 顺延次日（同一窗口段）。"""
        # 假设窗口 09:00-09:05（人为收窄），4 条内容同平台 → 至少 1 条次日
        narrow_window = PlatformAPI(
            kind="api", windows=["09:00-09:05"], accounts=[],
        )
        conn = _conn(tmp_path)
        cs = [
            _make_content(conn, id=f"c_appv000{i}", title=f"内容{i}")
            for i in range(4)
        ]
        result = plan(
            cs, {"x": narrow_window},
            existing_publications=[],
            now_iso=_NOW_ISO, min_gap_hours=4,
            cross_platform_gap_minutes=30,
            tz_name="Asia/Shanghai",
        )
        assert len(result.publications) == 4
        # 至少 1 条排在 2026-07-06
        from pipeline.scheduler import _parse_iso_utc
        days = {_parse_iso_utc(p.scheduled_at).date().isoformat()
                for p in result.publications}
        assert "2026-07-06" in days


class TestPlanIdempotency:
    def test_existing_publications_not_changed(
        self, tmp_path: Path
    ) -> None:
        """已有排期不改变——只新增；不会出现重复或修改时间。"""
        conn = _conn(tmp_path)
        c = _make_content(conn)
        existing = [
            Publication(
                id="p_existing01", content_id=c.id, platform="x",
                account_id="main",
                scheduled_at="2026-07-05T10:00:00+00:00",
                published_at=None, platform_post_id=None,
                platform_url=None, error=None,
                retry_count=0,
                status=PublicationStatus.QUEUED.value,
                created_at=_NOW_ISO, updated_at=_NOW_ISO,
            )
        ]
        result = plan(
            [c], {"x": _x_platform()},
            existing_publications=existing,
            now_iso=_NOW_ISO, min_gap_hours=4,
            cross_platform_gap_minutes=30,
            tz_name="Asia/Shanghai",
        )
        # 已有 p_existing01 不在 result.publications（plan 只产出新增）
        assert all(
            p.id != "p_existing01" for p in result.publications
        )
        # 但新排期不与已有冲突（间隔 ≥ 4h）
        new_pub = result.publications[0]
        from pipeline.scheduler import _parse_iso_utc
        t_existing = _parse_iso_utc(existing[0].scheduled_at)
        t_new = _parse_iso_utc(new_pub.scheduled_at)
        gap_h = abs((t_new - t_existing).total_seconds() / 3600)
        assert gap_h >= 4.0

    def test_same_input_same_output(
        self, tmp_path: Path
    ) -> None:
        """同输入（同 content、同 now、同 seed）→ 同输出（重跑不漂移）。"""
        conn = _conn(tmp_path)
        c1 = _make_content(conn, id="c_appv0001")
        c2 = _make_content(conn, id="c_appv0002")

        r1 = plan(
            [c1, c2], {"x": _x_platform()},
            existing_publications=[],
            now_iso=_NOW_ISO, min_gap_hours=4,
            cross_platform_gap_minutes=30,
            tz_name="Asia/Shanghai",
        )
        r2 = plan(
            [c1, c2], {"x": _x_platform()},
            existing_publications=[],
            now_iso=_NOW_ISO, min_gap_hours=4,
            cross_platform_gap_minutes=30,
            tz_name="Asia/Shanghai",
        )
        assert len(r1.publications) == len(r2.publications)
        for p1, p2 in zip(r1.publications, r2.publications):
            assert p1.content_id == p2.content_id
            assert p1.platform == p2.platform
            assert p1.scheduled_at == p2.scheduled_at


class TestPlanValidation:
    def test_empty_contents_returns_empty(
        self, tmp_path: Path
    ) -> None:
        result = plan(
            [], {"x": _x_platform()},
            existing_publications=[],
            now_iso=_NOW_ISO, min_gap_hours=4,
            cross_platform_gap_minutes=30,
            tz_name="Asia/Shanghai",
        )
        assert result.publications == []

    def test_platform_with_no_windows_skipped(
        self, tmp_path: Path
    ) -> None:
        """平台没配 windows → 该平台不出排期（不抛错）。"""
        conn = _conn(tmp_path)
        c = _make_content(conn)
        empty = PlatformAPI(kind="api", windows=[], accounts=[])
        result = plan(
            [c], {"x": empty, "toutiao": _toutiao_platform()},
            existing_publications=[],
            now_iso=_NOW_ISO, min_gap_hours=4,
            cross_platform_gap_minutes=30,
            tz_name="Asia/Shanghai",
        )
        # x 被跳过，只剩 toutiao
        platforms = {p.platform for p in result.publications}
        assert "x" not in platforms
        assert "toutiao" in platforms

    def test_min_gap_hours_enforced(
        self, tmp_path: Path
    ) -> None:
        """3 条内容同平台 → 相邻排期间隔 ≥ min_gap_hours。"""
        conn = _conn(tmp_path)
        cs = [_make_content(conn, id=f"c_appv000{i}") for i in range(3)]
        result = plan(
            cs, {"x": _x_platform()},
            existing_publications=[],
            now_iso=_NOW_ISO, min_gap_hours=4,
            cross_platform_gap_minutes=30,
            tz_name="Asia/Shanghai",
        )
        from pipeline.scheduler import _parse_iso_utc
        times = sorted(_parse_iso_utc(p.scheduled_at) for p in result.publications)
        gaps = [
            (times[i+1] - times[i]).total_seconds() / 3600
            for i in range(len(times) - 1)
        ]
        for g in gaps:
            assert g >= 4.0, f"gap {g}h < min_gap_hours"