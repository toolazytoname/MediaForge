"""M2-5 — review 阶段编排。

对外契约：
  - ReviewDecision: 单条决策数据类
  - run_review(conn, *, date_str, output_root, webhook_url=None) -> ReviewResult
  - checklist_path / build_checklist_markdown / write_checklist
  - parse_review_markdown / apply_decisions / read_and_apply
  - notify_review

run_review 行为（按 ARCHITECTURE §3.5 + HARD_PARTS §5）：
  1. 读旧 REVIEW.md（如存在）→ 应用决策落库（幂等）
  2. 重新查询当前 gated 内容 → 生成新 REVIEW.md
  3. 若 webhook_url 非空 + 当日有 gated → 推 IM
  4. 返回 ReviewResult（generated/applied/rejected 计数）
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from pipeline.review.checklist import (
    build_checklist_markdown,
    checklist_path,
    write_checklist,
)
from pipeline.review.notify import notify_review
from pipeline.review.reader import (
    ReviewDecision,
    apply_decisions,
    parse_review_markdown,
    read_and_apply,
)


@dataclass(frozen=True)
class ReviewResult:
    """run_review 返回值——供 run.py 打印摘要。"""
    generated: int       # 写入 REVIEW.md 的 gated 条数
    applied: int         # 落库 approved 数
    rejected: int        # 落库 rejected_by_human 数


__all__ = [
    "ReviewDecision",
    "ReviewResult",
    "run_review",
    "checklist_path",
    "build_checklist_markdown",
    "write_checklist",
    "parse_review_markdown",
    "apply_decisions",
    "read_and_apply",
    "notify_review",
]


def run_review(
    conn: sqlite3.Connection,
    *,
    date_str: str,
    output_root: Path | str,
    now_iso: str,
    webhook_url: str | None = None,
    log_dir: Path | str = "logs",
) -> ReviewResult:
    """编排：先读后写（先应用人标记，再生成新清单）。

    顺序很关键：
      1. 先读：若人已在 REVIEW.md 上勾过，必须先把它们的 approved/rejected
         落库，再生成新清单——否则同一条 gated 内容会在新清单里重复出现
      2. 后写：基于最新 gated 状态生成
      3. 通知：仅在有内容且 webhook_url 非空时发
    """
    review_file = checklist_path(output_root, now_iso)

    # 1. 读旧 + 应用（HARD_PARTS §5 幂等：已非 gated 的会被跳过）
    applied, rejected = read_and_apply(
        conn, review_file, log_dir=log_dir,
    )

    # 2. 生成新清单
    path = write_checklist(
        conn, date_str=date_str, output_root=output_root, now_iso=now_iso,
    )
    # 当前 gated 条数 = 新清单里的条数
    new_md = build_checklist_markdown(
        conn, date_str=date_str, output_root=output_root,
    )
    generated = _count_gated_blocks(new_md)

    # 3. 通知：仅当日有内容
    if generated > 0:
        notify_review(
            webhook_url=webhook_url, count=generated, review_path=path,
        )

    return ReviewResult(
        generated=generated, applied=applied, rejected=rejected,
    )


def _count_gated_blocks(md: str) -> int:
    """数 REVIEW.md 里 `## [c_xxx]` 节数（= gated 条数）。

    内容 id 实际是 8 hex chars（utils.ids.new_id 生成），但人手编辑时可能
    误写成含其他字符的"易读 id"——这里用宽松小写字母+下划线保持鲁棒。
    """
    import re
    return len(re.findall(r"^##\s+\[c_[0-9a-z_]+\]", md, re.MULTILINE))