"""M3-1 scheduler：错峰排期纯函数（ARCHITECTURE §3.6 + HARD_PARTS §8）。

核心契约：
  plan(approved_contents, platform_configs, existing_publications,
       now_iso, *, min_gap_hours, cross_platform_gap_minutes, tz_name)
       → PlanResult

  返回新增的 Publication 记录（不重复已有排期，不动数据库）。

排期规则：
  - 每平台黄金时段 windows（如 ["07:00-09:00", "18:00-20:00"]，本地时区）
  - 窗口内随机取点，避开整点 ±3min
  - 同平台同账号两条间隔 ≥ min_gap_hours（默认 4h）
  - 同内容跨平台错开 ≥ cross_platform_gap_minutes（默认 30min）
  - 当日窗口排满 → 顺延次日同一窗口
  - 种子 = content_id + platform → 重跑结果可复现（HARD_PARTS §8）

存储：UTC ISO8601（TECH_SPEC §10）；展示：本地时区（Asia/Shanghai）。
"""
from __future__ import annotations

import hashlib
import random
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable
from zoneinfo import ZoneInfo

from pipeline.config import PlatformAPI, PlatformPlaywright
from pipeline.models import Content, Publication, PublicationStatus
from pipeline.utils.ids import new_id


# ── 数据类 ──────────────────────────────────────────────────


@dataclass(frozen=True)
class Window:
    """本地时区下的一个黄金时段窗口（HH:MM-HH:MM 解析结果）。"""
    start_h: int
    start_m: int
    end_h: int
    end_m: int

    def start_time(self) -> time:
        return time(self.start_h, self.start_m)

    def end_time(self) -> time:
        return time(self.end_h, self.end_m)

    def duration_minutes(self) -> int:
        return (self.end_h * 60 + self.end_m) - (self.start_h * 60 + self.start_m)


@dataclass(frozen=True)
class PlanResult:
    publications: list[Publication] = field(default_factory=list)


# ── 公开：窗口解析 ──────────────────────────────────────────


_WINDOW_RE = re.compile(
    r"^(\d{2}):(\d{2})-(\d{2}):(\d{2})$"
)


def _parse_window(spec: str) -> Window:
    """'HH:MM-HH:MM' → Window。不合规抛 ValueError。"""
    m = _WINDOW_RE.match(spec.strip())
    if not m:
        raise ValueError(f"invalid window spec: {spec!r}")
    sh, sm, eh, em = (int(g) for g in m.groups())
    if sh > 23 or eh > 23 or sm > 59 or em > 59:
        raise ValueError(f"invalid window spec: {spec!r}")
    if (eh, em) <= (sh, sm):
        raise ValueError(f"window end <= start: {spec!r}")
    return Window(sh, sm, eh, em)


def _platform_windows(plat) -> list[Window]:
    """从 PlatformAPI / PlatformPlaywright 取 windows 列表。"""
    return [_parse_window(w) for w in (plat.windows or [])]


# ── 公开：主入口 plan() ─────────────────────────────────────


def plan(
    approved_contents: Iterable[Content],
    platform_configs: dict[str, PlatformAPI | PlatformPlaywright],
    existing_publications: Iterable[Publication],
    now_iso: str,
    *,
    min_gap_hours: int,
    cross_platform_gap_minutes: int,
    tz_name: str = "Asia/Shanghai",
) -> PlanResult:
    """为 approved 内容 × 启用的平台 计算新排期。

    重要：此函数是纯函数，不写 DB。落库由 cmd_schedule 调用 db.insert_publication
    配合 UNIQUE(content_id, platform, account_id) 约束防重复（HARD_PARTS §1）。

    参数：
      - approved_contents: 待排期的内容列表
      - platform_configs: {"x": PlatformAPI, "toutiao": PlatformPlaywright, ...}
      - existing_publications: 已有排期（含跨时间跨账号所有历史）
      - now_iso: 当前 UTC ISO8601（用于"今日窗口已过则顺延次日"判断）
      - min_gap_hours: 同平台同账号间隔
      - cross_platform_gap_minutes: 同内容跨平台错开
      - tz_name: 本地时区名（默认 Asia/Shanghai）

    返回：PlanResult，含新增的 Publication 列表（不含已有）
    """
    now_utc = _parse_iso_utc(now_iso)
    tz = ZoneInfo(tz_name)
    existing = list(existing_publications)
    contents = list(approved_contents)

    new_pubs: list[Publication] = []

    # 已有排期按平台分组（用于检查同平台间隔）
    by_platform: dict[str, list[Publication]] = {}
    for p in existing:
        by_platform.setdefault(p.platform, []).append(p)

    for content in contents:
        # 同一内容按 platforms 顺序排（保证跨平台错开生效）
        platforms = list(platform_configs.keys())
        last_for_content: Publication | None = None

        for plat_name in platforms:
            plat_cfg = platform_configs[plat_name]
            windows = _platform_windows(plat_cfg)
            if not windows:
                # 平台未配 windows → 跳过（不抛错，HARD_PARTS §2 容错）
                continue

            # 选时间点：先在已有同平台排期 + 同内容排期之间找空隙
            plat_existing = by_platform.get(plat_name, [])
            existing_for_plat = [p.scheduled_at for p in plat_existing]

            scheduled = _pick_slot(
                content=content,
                platform=plat_name,
                windows=windows,
                now_local=now_utc.astimezone(tz),
                tz=tz,
                existing_for_platform=existing_for_plat,
                min_gap_hours=min_gap_hours,
                last_for_content=last_for_content,
                cross_platform_gap_minutes=cross_platform_gap_minutes,
            )
            if scheduled is None:
                # 排不下（理论上不应发生，留扩展点）
                continue

            account_id = _first_account(plat_cfg)
            pub = Publication(
                id=new_id("p"),
                content_id=content.id,
                platform=plat_name,
                account_id=account_id,
                scheduled_at=scheduled.astimezone(timezone.utc).isoformat(),
                published_at=None,
                platform_post_id=None,
                platform_url=None,
                error=None,
                retry_count=0,
                status=PublicationStatus.QUEUED.value,
                created_at=now_iso,
                updated_at=now_iso,
            )
            new_pubs.append(pub)
            by_platform.setdefault(plat_name, []).append(pub)
            last_for_content = pub

    return PlanResult(publications=new_pubs)


# ── 内部：时间选取 ─────────────────────────────────────────


def _pick_slot(
    *,
    content: Content,
    platform: str,
    windows: list[Window],
    now_local: datetime,
    tz: ZoneInfo,
    existing_for_platform: list[str],
    min_gap_hours: int,
    last_for_content: Publication | None,
    cross_platform_gap_minutes: int,
) -> datetime | None:
    """为一条 content × platform 在窗口内挑一个时间点。

    策略：
      1. 种子 random.Random(content_id + platform) → 可复现
      2. 遍历每个候选日期（今日优先 → 次日 → ...）
      3. 在每个窗口内随机取一个 timestamp
      4. 校验：避开整点 ±3min + 与 existing_for_platform 间隔 ≥ min_gap_hours +
         与 last_for_content 间隔 ≥ cross_platform_gap_minutes
      5. 找不到 → 返回 None
    """
    seed = _seed_for(content.id, platform)
    rng = random.Random(seed)
    min_gap = timedelta(hours=min_gap_hours)
    cross_gap = timedelta(minutes=cross_platform_gap_minutes)

    # 已有排期转 UTC datetime 用于比较
    existing_dts = sorted(
        _parse_iso_utc(s) for s in existing_for_platform
    )
    last_dt = (
        _parse_iso_utc(last_for_content.scheduled_at)
        if last_for_content is not None else None
    )

    # 候选日期：从今日开始，最多向前看 14 天（顺延上限）
    for day_offset in range(14):
        candidate_date = (now_local + timedelta(days=day_offset)).date()
        # 每个窗口各尝试最多 20 次
        for window in windows:
            for _ in range(20):
                ts = _sample_in_window(
                    candidate_date, window, rng, tz,
                )
                # 校验整点规避
                if not _away_from_hour(ts):
                    continue
                # 校验与同平台已有排期
                if any(
                    abs(ts - e) < min_gap for e in existing_dts
                ):
                    continue
                # 校验与同内容跨平台已有排期
                if last_dt is not None and abs(ts - last_dt) < cross_gap:
                    continue
                # 校验不在过去（顺延次日时，原"今日"窗口已过也允许——
                # 但通常 schedule 是为未来排期，所以跳过过去时间）
                if ts < now_local:
                    continue
                return ts
    return None


def _sample_in_window(
    d: date, window: Window, rng: random.Random, tz: ZoneInfo
) -> datetime:
    """窗口内均匀采样一个本地时间。offset 溢出时正确进位到小时。"""
    total_min = window.duration_minutes()
    offset = rng.randint(0, max(total_min - 1, 0))
    start_total = window.start_h * 60 + window.start_m
    abs_min = start_total + offset
    h = abs_min // 60
    m = abs_min % 60
    local = datetime.combine(d, time(h, m), tzinfo=tz)
    return local


def _away_from_hour(ts: datetime) -> bool:
    """ts 不在整点 ±3min（HARD_PARTS §8）。"""
    return not (ts.minute <= 3 or ts.minute >= 57)


def _seed_for(content_id: str, platform: str) -> int:
    """content_id + platform → 稳定种子（HARD_PARTS §8 复现性）。"""
    h = hashlib.sha256(f"{content_id}|{platform}".encode()).digest()
    return int.from_bytes(h[:4], "big")


def _parse_iso_utc(s: str) -> datetime:
    """ISO8601 字符串 → 带 tzinfo 的 datetime（始终视为 UTC 或转 UTC）。"""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _first_account(plat) -> str:
    """从 platform_cfg.accounts 取第一个账号 id；空则用 '_default_'。"""
    accs = getattr(plat, "accounts", None) or []
    if accs:
        return getattr(accs[0], "id", None) or "_default_"
    return "_default_"


__all__ = [
    "Window",
    "PlanResult",
    "plan",
    "_parse_window",        # 暴露给测试
    "_parse_iso_utc",       # 暴露给测试
    "_platform_windows",    # 暴露给测试
]