"""周视图日历工具（TECH_SPEC §7 + M4-4 任务）。

按 anchor_date 所在周（周一 → 周日）把 publications 分桶。
- anchor 缺省 = 今天
- 提供 htmx-friendly 的纯函数 + 模板片段
- 纯 UTC 计算，本地展示由调用方按需转换（HARD_PARTS §8）
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone


@dataclass(frozen=True)
class WeekBucket:
    """单周 7 天的分桶（Mon → Sun）。"""
    week_start: date           # 周一（含）
    week_end: date             # 周日（含）
    days: tuple[date, ...]     # 长度 = 7
    by_day: dict[date, list]   # date → 该日 publications
    prev_week: str             # 上周 anchor (ISO date 字符串)
    next_week: str             # 下周 anchor
    this_week: str             # 本周 anchor


def _parse_iso(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def week_around(anchor: date) -> tuple[date, date]:
    """anchor 所在周的（周一, 周日）。"""
    monday = anchor - timedelta(days=anchor.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def bucket_week(
    publications: list,
    anchor_iso: str | None = None,
) -> WeekBucket:
    """把 publications 按 scheduled_at（UTC）分桶到 anchor 所在周。

    Args:
        publications: list of `Publication` dataclass（任何含
            `scheduled_at: str` ISO8601 UTC 的对象都可）
        anchor_iso: 周锚定日（ISO date 或 ISO datetime），None = 今天 UTC

    Returns:
        WeekBucket：7 天 + by_day + prev/next/this anchor ISO date
    """
    if anchor_iso is None:
        anchor = datetime.now(timezone.utc).date()
    else:
        try:
            anchor = date.fromisoformat(anchor_iso[:10])
        except ValueError:
            anchor = datetime.now(timezone.utc).date()

    monday, sunday = week_around(anchor)
    days = tuple(monday + timedelta(days=i) for i in range(7))

    by_day: dict[date, list] = {d: [] for d in days}
    for pub in publications:
        try:
            sched_dt = _parse_iso(pub.scheduled_at)
        except (ValueError, TypeError, AttributeError):
            continue
        d = sched_dt.date()
        if d in by_day:
            by_day[d].append(pub)

    # 按时间排序每个 bucket
    for d in days:
        by_day[d].sort(key=lambda p: p.scheduled_at)

    return WeekBucket(
        week_start=monday,
        week_end=sunday,
        days=days,
        by_day=by_day,
        prev_week=(monday - timedelta(days=7)).isoformat(),
        next_week=(monday + timedelta(days=7)).isoformat(),
        this_week=anchor.isoformat(),
    )


__all__ = ["WeekBucket", "bucket_week", "week_around"]