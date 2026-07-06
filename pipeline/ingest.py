"""ingest 编排：拉取所有启用的数据源 → 入库 → 去重（HARD_PARTS §5/§8）。

行为契约：
  - 遍历传入的 SourceAdapter 列表，逐源 fetch → 域名安全校验 → 逐条 try_insert_topic
  - 单源抛异常（SourceError 或其他）→ 打印 warning 到 stderr，跳过该源
  - 全部跑完后打印摘要到 stdout: `ingest: N fetched, N new, N dup`
  - 不抛异常：失败源记入 IngestResult.failed_sources（不阻断批次）

幂等保证：依赖 try_insert_topic 的 INSERT OR IGNORE + content_hash UNIQUE，
二次运行同一批条目 → fetched>0, new=0, dup=fetched。

域名安全校验：M1-5 借鉴 Horizon/sansan0 防数据投毒。当 source 在
KNOWN_DOMAIN_RULES 登记预期域名时，丢弃 URL 不匹配的条目，计入
dropped_safety（不计入 fetched，因为是 fetch 后丢弃）。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field

from pipeline import db
from pipeline.sources.base import SourceAdapter
from pipeline.sources.safety import resolve_expected_domain, validate_items


@dataclass(frozen=True)
class IngestResult:
    """一次 ingest 编排的统计结果。

    Attributes:
        fetched: 所有源 fetch 出来的总条目数（去重前；含安全校验丢弃前的全量）
        new: 实际新入库的条目数
        dup: 因 content_hash 已存在而跳过的条目数
        failed_sources: 因异常被跳过的源 name 列表
        dropped_safety: M1-5 域名校验丢弃的条目数（M1-5 字段，向后兼容默认 0）
    """
    fetched: int
    new: int
    dup: int
    failed_sources: tuple[str, ...]
    dropped_safety: int = field(default=0)


def run_ingest(
    conn,
    sources: list[SourceAdapter],
    now: str,
) -> IngestResult:
    """执行一次 ingest 编排。

    Args:
        conn: SQLite 连接（已 init_db）
        sources: 启用的 SourceAdapter 列表（来自 build_sources）
        now: ISO8601 UTC 时间字符串（同时填 created_at / updated_at）

    Returns:
        IngestResult 不可变结果。失败源不抛异常，记入 failed_sources。
    """
    total_fetched = 0
    total_new = 0
    total_dup = 0
    total_dropped_safety = 0
    failed: list[str] = []

    for src in sources:
        try:
            items = src.fetch()
        except Exception as e:
            # HARD_PARTS §8：单源失败不阻断批次——warning + 跳过
            print(
                f"ingest: WARN source={src.name} skipped: "
                f"{type(e).__name__}: {e}",
                file=sys.stderr,
            )
            failed.append(src.name)
            continue

        # M1-5 域名安全校验：源登记了预期域名时丢弃不匹配的 URL
        # 不在 KNOWN_DOMAIN_RULES 里的源一律不校验（fail-open）
        expected_domain = resolve_expected_domain(src.name)
        if expected_domain is not None:
            kept_items, dropped, dropped_reasons = validate_items(
                items, expected_domain,
            )
            if dropped > 0:
                # 摘要日志：第一条原因 + 总数；全量留 trace 级别（如需）
                first_title, first_reason = dropped_reasons[0]
                print(
                    f"ingest: WARN source={src.name} "
                    f"dropped {dropped} item(s) by domain check "
                    f"(expected={expected_domain}); "
                    f"first: {first_title!r} → {first_reason}",
                    file=sys.stderr,
                )
                total_dropped_safety += dropped
                items = kept_items

        for item in items:
            _, is_new = db.try_insert_topic(conn, item, src.name, now)
            total_fetched += 1
            if is_new:
                total_new += 1
            else:
                total_dup += 1

    print(
        f"ingest: {total_fetched} fetched, "
        f"{total_new} new, {total_dup} dup"
    )

    return IngestResult(
        fetched=total_fetched,
        new=total_new,
        dup=total_dup,
        failed_sources=tuple(failed),
        dropped_safety=total_dropped_safety,
    )