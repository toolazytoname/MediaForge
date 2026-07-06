"""每周报告生成（M6-2 + HARD_PARTS §3 要点 4）。

每周一自动生成 output/weekly-report.md，含 4 个 section：
1. 概览：发布数 / 门禁通过率 / 丢弃率
2. 平台表现：每平台 top3 + bottom3（按 views/likes 排序）
3. LLM 成本：本周期 llm_calls 表统计
4. 门禁校准：分数直方图 + 分数 vs 实际表现相关性散点（HARD_PARTS §3）

**数据源**：state.db（topics / contents / publications / metrics / llm_calls）
**周期**：默认过去 7 天（from now - 7d to now）
**输出**：Markdown 文件 + stdout 摘要
"""
from __future__ import annotations

import sqlite3
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


# ── 数据结构 ────────────────────────────────────────────


@dataclass(frozen=True)
class WeeklyOverview:
    """报告概览。"""
    period_start: str          # ISO8601
    period_end: str
    topics_raw: int
    contents_created: int
    gated_count: int
    approved_count: int
    discarded_count: int
    failed_count: int
    published_count: int
    scheduled_count: int
    gate_pass_rate: float      # gated / (gated + discarded)
    discard_rate: float        # discarded / (gated + discarded)


@dataclass(frozen=True)
class PlatformRanking:
    """单条 content 的表现行。"""
    content_id: str
    pillar: str | None
    title: str
    platform: str
    views: int
    likes: int
    gate_score: float | None


@dataclass(frozen=True)
class CostByStage:
    """单 stage 成本。"""
    stage: str
    call_count: int
    total_cost_usd: float
    total_input_tokens: int
    total_output_tokens: int


@dataclass(frozen=True)
class GateHistogramBucket:
    """门禁分直方图单 bucket。"""
    score_range: str           # "0-6", "6-12", ...
    count: int


@dataclass(frozen=True)
class WeeklyReport:
    """完整报告（多 section）。"""
    overview: WeeklyOverview
    top_by_platform: dict[str, list[PlatformRanking]]
    bottom_by_platform: dict[str, list[PlatformRanking]]
    costs: list[CostByStage]
    gate_histogram: list[GateHistogramBucket]
    correlation_gate_to_views: float | None    # Pearson r；无数据时 None


# ── helpers ─────────────────────────────────────────────


def _parse_iso(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ── 数据查询 ────────────────────────────────────────────


def _collect_overview(
    conn: sqlite3.Connection, *, start: datetime, end: datetime,
) -> WeeklyOverview:
    """本周期的内容流转统计。"""
    start_iso = start.isoformat()
    end_iso = end.isoformat()

    topics_raw = conn.execute(
        "SELECT COUNT(*) FROM topics WHERE created_at >= ? AND created_at < ?",
        (start_iso, end_iso),
    ).fetchone()[0]
    contents_created = conn.execute(
        "SELECT COUNT(*) FROM contents WHERE created_at >= ? AND created_at < ?",
        (start_iso, end_iso),
    ).fetchone()[0]

    # contents 状态分布（整个 DB，因为 status 流转可能跨周期）
    rows = conn.execute(
        "SELECT status, COUNT(*) FROM contents GROUP BY status"
    ).fetchall()
    status_counts = {r[0]: r[1] for r in rows}
    gated_count = status_counts.get("gated", 0)
    approved_count = status_counts.get("approved", 0)
    discarded_count = status_counts.get("discarded", 0)
    failed_count = status_counts.get("failed", 0)

    # publications 状态分布
    pub_rows = conn.execute(
        "SELECT status, COUNT(*) FROM publications GROUP BY status"
    ).fetchall()
    pub_status = {r[0]: r[1] for r in pub_rows}
    published_count = pub_status.get("published", 0)
    scheduled_count = pub_status.get("queued", 0)

    # 门禁通过率 / 丢弃率（按 gated + discarded 总和算）
    total_judged = gated_count + discarded_count
    gate_pass_rate = (
        gated_count / total_judged if total_judged > 0 else 0.0
    )
    discard_rate = (
        discarded_count / total_judged if total_judged > 0 else 0.0
    )

    return WeeklyOverview(
        period_start=start_iso,
        period_end=end_iso,
        topics_raw=topics_raw,
        contents_created=contents_created,
        gated_count=gated_count,
        approved_count=approved_count,
        discarded_count=discarded_count,
        failed_count=failed_count,
        published_count=published_count,
        scheduled_count=scheduled_count,
        gate_pass_rate=gate_pass_rate,
        discard_rate=discard_rate,
    )


def _collect_platform_rankings(
    conn: sqlite3.Connection,
    *,
    published_after: datetime,
) -> tuple[dict[str, list[PlatformRanking]], dict[str, list[PlatformRanking]]]:
    """每平台 top3 / bottom3（按 views 排序；views 为 None 排后）。

    只看本周期之后 published 的 publications。
    """
    after_iso = published_after.isoformat()
    rows = conn.execute(
        """
        SELECT p.id, c.pillar, c.title, p.platform,
               COALESCE(SUM(m.views), 0) AS views,
               COALESCE(SUM(m.likes), 0) AS likes,
               c.gate_score_total
        FROM publications p
        JOIN contents c ON c.id = p.content_id
        LEFT JOIN metrics m ON m.publication_id = p.id
        WHERE p.status = 'published' AND p.published_at >= ?
        GROUP BY p.id
        """,
        (after_iso,),
    ).fetchall()

    by_platform: dict[str, list[PlatformRanking]] = {}
    for r in rows:
        rk = PlatformRanking(
            content_id=r[0], pillar=r[1], title=r[2], platform=r[3],
            views=int(r[4]), likes=int(r[5]),
            gate_score=r[6] if r[6] is not None else None,
        )
        by_platform.setdefault(r[3], []).append(rk)

    top = {p: sorted(items, key=lambda x: -x.views)[:3]
           for p, items in by_platform.items()}
    bottom = {p: sorted(items, key=lambda x: x.views)[:3]
              for p, items in by_platform.items()}
    return top, bottom


def _collect_costs(
    conn: sqlite3.Connection, *, start: datetime, end: datetime,
) -> list[CostByStage]:
    """本周期 LLM 成本（按 stage 分组）。"""
    rows = conn.execute(
        """
        SELECT stage,
               COUNT(*) AS calls,
               COALESCE(SUM(cost_usd), 0) AS total_cost,
               COALESCE(SUM(input_tokens), 0) AS total_in,
               COALESCE(SUM(output_tokens), 0) AS total_out
        FROM llm_calls
        WHERE created_at >= ? AND created_at < ?
        GROUP BY stage
        ORDER BY total_cost DESC
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    return [
        CostByStage(
            stage=r[0], call_count=int(r[1]),
            total_cost_usd=float(r[2]),
            total_input_tokens=int(r[3]),
            total_output_tokens=int(r[4]),
        )
        for r in rows
    ]


# 门禁分直方图 bucket 配置（HARD_PARTS §3 阈值）
_HISTOGRAM_BUCKETS = [
    (0, 6, "0-6"),
    (6, 12, "6-12"),
    (12, 18, "12-18"),
    (18, 24, "18-24"),
    (24, 30, "24-30"),
]


def _collect_gate_histogram(conn: sqlite3.Connection) -> list[GateHistogramBucket]:
    """门禁分分布直方图（HARD_PARTS §3 阈值 24 / 单维 6）。"""
    rows = conn.execute(
        "SELECT gate_score_total FROM contents "
        "WHERE gate_score_total IS NOT NULL"
    ).fetchall()
    scores = [float(r[0]) for r in rows]

    out: list[GateHistogramBucket] = []
    for lo, hi, label in _HISTOGRAM_BUCKETS:
        count = sum(1 for s in scores if lo <= s < hi)
        out.append(GateHistogramBucket(score_range=label, count=count))
    return out


def _collect_gate_to_views_correlation(conn: sqlite3.Connection) -> float | None:
    """门禁分 vs views 的 Pearson 相关（HARD_PARTS §3 要点 4）。

    无数据 → None。
    """
    rows = conn.execute(
        """
        SELECT c.gate_score_total, COALESCE(SUM(m.views), 0) AS views
        FROM contents c
        JOIN publications p ON p.content_id = c.id
        LEFT JOIN metrics m ON m.publication_id = p.id
        WHERE c.gate_score_total IS NOT NULL
        GROUP BY c.id
        HAVING SUM(m.views) IS NOT NULL
        """
    ).fetchall()
    if len(rows) < 2:
        return None
    xs = [float(r[0]) for r in rows]
    ys = [float(r[1]) for r in rows]
    try:
        return statistics.correlation(xs, ys)
    except statistics.StatisticsError:
        return None


# ── 报告生成 ────────────────────────────────────────────


def collect_weekly_report(
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
    window_days: int = 7,
) -> WeeklyReport:
    """收集本周报告所有数据。"""
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(days=window_days)
    overview = _collect_overview(conn, start=start, end=now)
    top, bottom = _collect_platform_rankings(conn, published_after=start)
    costs = _collect_costs(conn, start=start, end=now)
    histogram = _collect_gate_histogram(conn)
    corr = _collect_gate_to_views_correlation(conn)
    return WeeklyReport(
        overview=overview,
        top_by_platform=top,
        bottom_by_platform=bottom,
        costs=costs,
        gate_histogram=histogram,
        correlation_gate_to_views=corr,
    )


def render_markdown(report: WeeklyReport) -> str:
    """WeeklyReport → Markdown 字符串。"""
    o = report.overview
    lines: list[str] = []
    lines.append("# 周报")
    lines.append("")
    lines.append(f"**周期**：{o.period_start} → {o.period_end}")
    lines.append("")

    # 1. 概览
    lines.append("## 1. 概览")
    lines.append("")
    lines.append("| 指标 | 数量 |")
    lines.append("|------|------|")
    lines.append(f"| 本期新 topic | {o.topics_raw} |")
    lines.append(f"| 本期新 content | {o.contents_created} |")
    lines.append(f"| 门禁通过（gated） | {o.gated_count} |")
    lines.append(f"| 人工通过（approved） | {o.approved_count} |")
    lines.append(f"| 门禁丢弃（discarded） | {o.discarded_count} |")
    lines.append(f"| 创作失败（failed） | {o.failed_count} |")
    lines.append(f"| 已发布（published） | {o.published_count} |")
    lines.append(f"| 待发布（queued） | {o.scheduled_count} |")
    lines.append("")
    lines.append(f"- **门禁通过率**：{o.gate_pass_rate:.1%}")
    lines.append(f"- **门禁丢弃率**：{o.discard_rate:.1%}")
    lines.append("")

    # 2. 各平台 top3 / bottom3
    lines.append("## 2. 各平台表现")
    lines.append("")
    if not report.top_by_platform:
        lines.append("_本期无已发布 + 已收集 metrics 的内容_")
    else:
        for platform in sorted(report.top_by_platform):
            lines.append(f"### {platform}")
            lines.append("")
            top3 = report.top_by_platform[platform]
            bottom3 = report.bottom_by_platform.get(platform, [])
            if top3:
                lines.append("**Top 3**（按 views）：")
                lines.append("")
                lines.append("| content | pillar | views | likes | gate_score |")
                lines.append("|---------|--------|-------|-------|------------|")
                for r in top3:
                    gs = f"{r.gate_score:.1f}" if r.gate_score else "—"
                    lines.append(
                        f"| `{r.content_id}` | {r.pillar or '—'} | "
                        f"{r.views} | {r.likes} | {gs} |"
                    )
                lines.append("")
            if bottom3:
                lines.append("**Bottom 3**（按 views）：")
                lines.append("")
                lines.append("| content | pillar | views | likes | gate_score |")
                lines.append("|---------|--------|-------|-------|------------|")
                for r in bottom3:
                    gs = f"{r.gate_score:.1f}" if r.gate_score else "—"
                    lines.append(
                        f"| `{r.content_id}` | {r.pillar or '—'} | "
                        f"{r.views} | {r.likes} | {gs} |"
                    )
                lines.append("")

    # 3. LLM 成本
    lines.append("## 3. LLM 成本")
    lines.append("")
    if not report.costs:
        lines.append("_本期无 LLM 调用记录_")
    else:
        lines.append("| stage | 调用次数 | 费用 (USD) | input tokens | output tokens |")
        lines.append("|-------|----------|------------|--------------|---------------|")
        total_cost = 0.0
        total_calls = 0
        for c in report.costs:
            lines.append(
                f"| {c.stage} | {c.call_count} | "
                f"${c.total_cost_usd:.2f} | "
                f"{c.total_input_tokens} | {c.total_output_tokens} |"
            )
            total_cost += c.total_cost_usd
            total_calls += c.call_count
        lines.append(f"| **总计** | **{total_calls}** | **${total_cost:.2f}** | | |")
        lines.append("")

    # 4. 门禁校准（HARD_PARTS §3 要点 4）
    lines.append("## 4. 门禁校准")
    lines.append("")
    lines.append("### 分数直方图")
    lines.append("")
    lines.append("```")
    lines.extend(_render_histogram_ascii(report.gate_histogram))
    lines.append("```")
    lines.append("")
    lines.append("### 分数 vs 实际表现相关性")
    lines.append("")
    if report.correlation_gate_to_views is None:
        lines.append("_样本不足（< 2），相关性暂不可计算_")
    else:
        r = report.correlation_gate_to_views
        quality = (
            "**强**" if abs(r) > 0.7
            else "中等" if abs(r) > 0.4
            else "**弱**" if abs(r) > 0.2
            else "几乎无"
        )
        lines.append(
            f"Pearson r = {r:+.3f}（{quality}相关）；"
            f"|r| < 0.2 时门禁与表现脱钩，**建议重新校准锚点**。"
        )
    lines.append("")
    lines.append("---")
    lines.append(
        f"_生成于 {datetime.now(timezone.utc).isoformat()}；"
        f"下次校准建议：每周一自动生成_"
    )
    return "\n".join(lines)


def _render_histogram_ascii(buckets: list[GateHistogramBucket]) -> list[str]:
    """直方图 → ASCII bar（最大宽度 40 字符）。"""
    max_count = max((b.count for b in buckets), default=0)
    lines: list[str] = []
    for b in buckets:
        bar_len = (
            int(40 * b.count / max_count) if max_count > 0 else 0
        )
        bar = "█" * bar_len
        lines.append(f"{b.score_range:>5} | {bar} {b.count}")
    return lines


def write_weekly_report(
    conn: sqlite3.Connection,
    *,
    output_path: str | Path = "output/weekly-report.md",
    now: datetime | None = None,
) -> Path:
    """生成并写盘；返回最终文件路径。"""
    report = collect_weekly_report(conn, now=now)
    md = render_markdown(report)
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(md, encoding="utf-8")
    return p


__all__ = [
    "WeeklyReport",
    "WeeklyOverview",
    "PlatformRanking",
    "CostByStage",
    "GateHistogramBucket",
    "collect_weekly_report",
    "render_markdown",
    "write_weekly_report",
]