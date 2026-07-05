"""ingest 编排：拉取所有启用的数据源 → 入库 → 去重（HARD_PARTS §5/§8）。

行为契约：
  - 遍历传入的 SourceAdapter 列表，逐源 fetch → 逐条 try_insert_topic
  - 单源抛异常（SourceError 或其他）→ 打印 warning 到 stderr，跳过该源
  - 全部跑完后打印摘要到 stdout: `ingest: N fetched, N new, N dup`
  - 不抛异常：失败源记入 IngestResult.failed_sources（不阻断批次）

幂等保证：依赖 try_insert_topic 的 INSERT OR IGNORE + content_hash UNIQUE，
二次运行同一批条目 → fetched>0, new=0, dup=fetched。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

from pipeline import db
from pipeline.sources.base import SourceAdapter


@dataclass(frozen=True)
class IngestResult:
    """一次 ingest 编排的统计结果。

    Attributes:
        fetched: 所有源 fetch 出来的总条目数（去重前）
        new: 实际新入库的条目数
        dup: 因 content_hash 已存在而跳过的条目数
        failed_sources: 因异常被跳过的源 name 列表
    """
    fetched: int
    new: int
    dup: int
    failed_sources: tuple[str, ...]


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
    )