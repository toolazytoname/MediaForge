"""M2-5 — REVIEW.md 解析与落库（ARCHITECTURE §3.5）。

输入：output/<date>/REVIEW.md 文本
输出：决策列表 → 走 db.transition 落库（gated → approved | rejected_by_human）

人编辑约定：
  - "- [x] approve"    → 通过
  - "- [-] reject: 理由" → 打回（理由写在该行末，可空）
  - 两者同时出现 → reject 胜出（更保守）
  - 都未勾          → 跳过（保留 gated 状态，等人下次再审）

幂等（TECH_SPEC §9 + HARD_PARTS §5）：
  - 内容已非 gated 状态（已 approved/discarded/rejected...）→ 跳过
  - 内容 id 不存在           → 跳过 + log warning
  - 重复运行同文件           → 已落库的状态不会被覆盖
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from pipeline.db import get_content, transition
from pipeline.models import ContentStatus
from pipeline.utils.log import get_logger, log_event


_SECTION_RE = re.compile(r"^##\s+\[(c_[0-9a-z_]+)\]", re.MULTILINE)
_APPROVE_RE = re.compile(r"^-\s*\[x\]", re.MULTILINE)
# 拒绝匹配：`- [-] reject: <理由>` —— 模板占位行 `- [-] reject:`（理由为空）
# 视为未标记。原因非空才算人写了决定。这是必要的"模板-编辑"语义区分，
# 否则每次重新生成的清单会被 reader 误判为全部 reject。
# 注意：reject 后的冒号必须存在（拒收 `?`），否则 `(.+?)` 会吃掉占位行的尾冒号。
_REJECT_RE = re.compile(
    r"^-\s*\[-\]\s*reject:\s*(.+?)\s*$", re.MULTILINE
)


@dataclass(frozen=True)
class ReviewDecision:
    content_id: str
    decision: str          # 'approve' | 'reject'
    reason: str | None     # 仅 reject 时使用


# ── 解析 ───────────────────────────────────────────────────


def parse_review_markdown(text: str) -> list[ReviewDecision]:
    """从 REVIEW.md 文本中解析决策列表（不读 DB、不落库）。

    返回所有命中 `- [x]` / `- [-]` 的决策。未标记的 section 不出现。
    同 section 两者都有 → reject 胜出。
    """
    # 用 section 标题切割：找到每个 section 的起止行号
    matches = list(_SECTION_RE.finditer(text))
    decisions: list[ReviewDecision] = []
    for i, m in enumerate(matches):
        content_id = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        section = text[start:end]

        has_approve = bool(_APPROVE_RE.search(section))
        reject_match = _REJECT_RE.search(section)
        if reject_match:
            reason = reject_match.group(1).strip()
            # 模板占位行 reason 为空 → 视为未标记（不写入决策）
            if reason:
                decisions.append(ReviewDecision(content_id, "reject", reason))
        elif has_approve:
            decisions.append(ReviewDecision(content_id, "approve", None))
        # else: 未标记，不入决策
    return decisions


# ── 落库 ───────────────────────────────────────────────────


def apply_decisions(
    conn: sqlite3.Connection,
    decisions: Iterable[ReviewDecision],
    *,
    log_dir: Path | str = "logs",
) -> tuple[int, int]:
    """把决策落库：approved / rejected_by_human 转移。

    返回 (applied_approve_count, applied_reject_count)。

    边界：
      - content 不存在  → warn + skip
      - content 状态非 gated → skip（已处理过；幂等保证）
      - 转移抛 IllegalTransition / StaleState → 跳过单条 + warn
    """
    logger = get_logger("review", log_dir=log_dir)
    applied_approve = 0
    applied_reject = 0

    for d in decisions:
        existing = get_content(conn, d.content_id)
        if existing is None:
            log_event(
                logger, 30,  # WARNING
                f"review: content not found, skipping",
                stage="review", ref_id=d.content_id,
            )
            continue
        if existing.status != ContentStatus.GATED.value:
            # 已非 gated（人或别的流程动过）→ 跳过，幂等
            log_event(
                logger, 20,  # INFO
                f"review: content not in gated status, skipping "
                f"(status={existing.status})",
                stage="review", ref_id=d.content_id,
            )
            continue

        if d.decision == "approve":
            try:
                transition(
                    conn, "contents", d.content_id,
                    ContentStatus.GATED.value,
                    ContentStatus.APPROVED.value,
                )
                applied_approve += 1
                log_event(
                    logger, 20,
                    "review: approved",
                    stage="review", ref_id=d.content_id,
                )
            except Exception as e:
                log_event(
                    logger, 30,
                    f"review: approve failed: {e}",
                    stage="review", ref_id=d.content_id,
                )
        elif d.decision == "reject":
            try:
                # 落库：用 gate_verdict 字段存人审理由（schema 复用）
                # 单事务：UPDATE verdict + transition
                _reject_with_reason(
                    conn, d.content_id, d.reason or "",
                )
                applied_reject += 1
                log_event(
                    logger, 20,
                    f"review: rejected_by_human reason={d.reason!r}",
                    stage="review", ref_id=d.content_id,
                )
            except Exception as e:
                log_event(
                    logger, 30,
                    f"review: reject failed: {e}",
                    stage="review", ref_id=d.content_id,
                )
        else:
            # 防御性：未知决策类型
            log_event(
                logger, 30,
                f"review: unknown decision {d.decision!r}",
                stage="review", ref_id=d.content_id,
            )

    return applied_approve, applied_reject


def _reject_with_reason(
    conn: sqlite3.Connection, content_id: str, reason: str
) -> None:
    """rejected_by_human + 写入人审理由到 gate_verdict 字段。

    单事务：先 UPDATE verdict → 再 transition（乐观锁）。
    """
    from pipeline.db import now_utc

    cur = conn.execute(
        "UPDATE contents SET gate_verdict=?, updated_at=? "
        "WHERE id=? AND status=?",
        (f"REJECTED_BY_HUMAN: {reason}".strip(), now_utc(),
         content_id, ContentStatus.GATED.value),
    )
    if cur.rowcount != 1:
        # 行不存在 或 状态已变 → 抛 StaleState
        existing = conn.execute(
            "SELECT status FROM contents WHERE id=?", (content_id,)
        ).fetchone()
        if existing is None:
            from pipeline.utils.errors import IllegalTransition
            raise IllegalTransition(
                "contents", ContentStatus.GATED.value,
                ContentStatus.REJECTED_BY_HUMAN.value,
            )
        from pipeline.utils.errors import StaleState
        raise StaleState(
            "contents", content_id, ContentStatus.GATED.value,
            existing["status"],
        )
    conn.commit()

    # 再走 transition 把状态从 gated 推到 rejected_by_human
    transition(
        conn, "contents", content_id,
        ContentStatus.GATED.value,
        ContentStatus.REJECTED_BY_HUMAN.value,
    )


# ── 便捷：从文件解析并落库 ─────────────────────────────────


def read_and_apply(
    conn: sqlite3.Connection,
    review_path: Path,
    *,
    log_dir: Path | str = "logs",
) -> tuple[int, int]:
    """读 REVIEW.md → 解析 → 落库。文件不存在 → 返回 (0, 0)。"""
    if not review_path.exists():
        return 0, 0
    text = review_path.read_text(encoding="utf-8")
    decisions = parse_review_markdown(text)
    return apply_decisions(conn, decisions, log_dir=log_dir)