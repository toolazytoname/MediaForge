"""门禁编排（M2-2）。

流程（每条 draft content）：
  1. critic.critique_one → 区分可重写 vs 不可重写
  2. 若 needs_rewrite 且次数 < max_rewrites → canonical.rewrite_one → 回到 1
  3. scorer.score_one → 独立评分（走 critical 档）
  4. decision.decide_gate → gated / discarded
  5. 落 critique.md + 更新 contents 表（gate_scores/verdict/total）
  6. db.transition draft → gated | discarded
  7. discarded 的文件保留（HARD_PARTS §5：可事后分析）

异常策略（TECH_SPEC §8）：
  - 单条 GateError / CreateError → skip 该条 + transition DRAFT→FAILED，继续
  - BudgetExceeded / StaleState / IllegalTransition → 系统性错误，终止整批
  - 任何其他未预期异常 → 同样 DRAFT→FAILED + 终止单条
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from pipeline import db as db_mod
from pipeline.config import GateConfig
from pipeline.creators.canonical import rewrite_one
from pipeline.gate.anchors_loader import AnchorsBundle, load_anchors
from pipeline.gate.critic import (
    CritiqueResult,
    critique_one,
    render_critique_text,
)
from pipeline.gate.decision import (
    Action,
    GateDecision,
    REVISABLE_CATEGORIES,
    decide_gate,
    decide_revision_action,
)
from pipeline.gate.scorer import ScoreResult, score_one
from pipeline.models import Content, ContentStatus
from pipeline.utils.errors import (
    BudgetExceeded,
    CreateError,
    GateError,
    IllegalTransition,
    StaleState,
)


# ── 结果数据类 ────────────────────────────────────────────

@dataclass(frozen=True)
class ContentGateOutcome:
    """单条 content 的门禁结果。"""
    content_id: str
    final_status: str          # gated | discarded | failed
    score_total: int | None
    score: ScoreResult | None
    critique: CritiqueResult | None
    rewrites: int
    decision: GateDecision | None
    reason: str = ""           # 简要说明


@dataclass(frozen=True)
class GateRunResult:
    """门禁编排整体结果。"""
    outcomes: tuple[ContentGateOutcome, ...] = field(default_factory=tuple)
    gated_count: int = 0
    discarded_count: int = 0
    failed_count: int = 0

    @property
    def processed(self) -> int:
        return len(self.outcomes)


# ── helpers ────────────────────────────────────────────────

def _write_critique_md(
    content_dir: Path,
    critic: CritiqueResult | None,
    score: ScoreResult | None,
    decision: GateDecision | None,
    rewrites: int,
    reason: str,
) -> Path:
    """落 critique.md 到 output/<date>/<content_id>/。"""
    content_dir.mkdir(parents=True, exist_ok=True)
    path = content_dir / "critique.md"
    status_label = decision.action.name if decision else "DISCARDED"
    lines: list[str] = []
    lines.append("# 门禁评审记录")
    lines.append("")
    lines.append(f"- 状态：**{status_label}**")
    lines.append(f"- 重写轮数：{rewrites}")
    lines.append(f"- 说明：{reason or '（无）'}")
    lines.append("")
    if decision is not None:
        lines.append("## 最终判定")
        lines.append("")
        lines.append(
            f"- 分数：info={decision.scores['info']} "
            f"fun={decision.scores['fun']} view={decision.scores['view']} "
            f"= 总 {decision.score}"
        )
        lines.append(f"- 评语：{decision.verdict or '（无）'}")
        lines.append(f"- 通过门禁：{'是' if decision.allow_publish else '否'}")
        lines.append("")
    if critic is not None:
        lines.append("## Critic 轮")
        lines.append("")
        lines.append(render_critique_text(critic))
        lines.append("")
    if score is not None:
        lines.append("## Scorer 原始响应")
        lines.append("")
        lines.append("```json")
        lines.append(
            json.dumps(
                {
                    "info": score.info, "fun": score.fun, "view": score.view,
                    "problems": [
                        {
                            "category": p.category, "severity": p.severity,
                            "message": p.message, "evidence": p.evidence,
                        }
                        for p in score.problems
                    ],
                    "verdict": score.verdict,
                },
                ensure_ascii=False, indent=2,
            )
        )
        lines.append("```")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _read_canonical(content: Content) -> str:
    path = Path(content.canonical_path)
    return path.read_text(encoding="utf-8")


def _save_gate_scores_no_commit(
    conn: sqlite3.Connection,
    content: Content,
    decision: GateDecision,
    now: str,
) -> None:
    """把 3 维分数 + 评语落 contents 表。

    ⚠️ 不 commit — 调用方用 `with conn:` 把 score-save + status-transition
    包在同一事务里，避免 StaleState 后留下半完成行（审计 Bug 2）。
    """
    conn.execute(
        """
        UPDATE contents
        SET gate_score_total=?, gate_scores=?, gate_verdict=?, updated_at=?
        WHERE id=?
        """,
        (
            float(decision.score),
            json.dumps(decision.scores, ensure_ascii=False),
            decision.verdict or None,
            now,
            content.id,
        ),
    )


def _mark_failed(
    conn: sqlite3.Connection, content_id: str, reason: str
) -> None:
    """DRAFT → FAILED（审计 Bug 4：所有异常路径必须先转移状态）。"""
    try:
        db_mod.transition(
            conn, "contents", content_id,
            from_status=ContentStatus.DRAFT.value,
            to_status=ContentStatus.FAILED.value,
        )
    except (StaleState, IllegalTransition):
        # 行已被改（如并发）—— 不抛，再走外层 catch
        pass


def _save_blocker_meta(
    conn: sqlite3.Connection, content_id: str, reason: str, now: str
) -> None:
    """blocker 路径也写 gate_verdict，便于审计（Bug Smell 2）。"""
    try:
        conn.execute(
            """
            UPDATE contents SET gate_verdict=?, updated_at=? WHERE id=?
            """,
            (reason, now, content_id),
        )
        conn.commit()
    except sqlite3.Error:
        pass


# ── 单条 content 编排 ────────────────────────────────────

def _process_one(
    conn: sqlite3.Connection,
    content: Content,
    *,
    anchors: AnchorsBundle,
    gate_cfg: GateConfig,
    now: str,
) -> ContentGateOutcome:
    """对单条 content 走完批判 → 评分 → 转移。"""
    canonical_md = _read_canonical(content)
    content_dir = Path(content.canonical_path).parent

    # 1. 第一轮 critic
    try:
        critic1 = critique_one(
            title=content.title,
            canonical_md=canonical_md,
            conn=conn,
            ref_id=content.id,
        )
    except (GateError, CreateError) as e:
        _mark_failed(conn, content.id, f"critic: {e}")
        return ContentGateOutcome(
            content_id=content.id,
            final_status=ContentStatus.FAILED.value,
            score_total=None, score=None, critique=None,
            rewrites=0, decision=None,
            reason=f"critic: {e}",
        )

    # 2. 决定是否触发重写
    revision_action = decide_revision_action(
        has_rewrite_issues=critic1.needs_rewrite,
        problems=critic1.problems,
    )

    # 1.5 若是 blocker 类问题 → 不走重写也不走 scorer，直接 discarded
    if revision_action == Action.BLOCK:
        reason = f"blocker: {len(critic1.problems)} blocker/fact/risk 问题"
        _save_blocker_meta(conn, content.id, reason, now)
        _write_critique_md(content_dir, critic1, None, None, 0, reason)
        db_mod.transition(
            conn, "contents", content.id,
            from_status=ContentStatus.DRAFT.value,
            to_status=ContentStatus.DISCARDED.value,
        )
        return ContentGateOutcome(
            content_id=content.id,
            final_status=ContentStatus.DISCARDED.value,
            score_total=None, score=None, critique=critic1,
            rewrites=0, decision=None,
            reason=reason,
        )

    # GATE 分支（无问题 / critic 判断不可改）→ 直接进 scorer
    rewrites = 0
    last_critic = critic1
    canonical_after_rewrite = canonical_md

    # 仅 REVISE 才走重写循环
    if revision_action == Action.REVISE:
        while rewrites < gate_cfg.max_rewrites:
            revisable = tuple(
                p for p in last_critic.problems
                if p.category in REVISABLE_CATEGORIES or p.auto_fixable
            )
            if not revisable:
                break
            critique_text = render_critique_text(
                CritiqueResult(
                    problems=revisable,
                    summary=last_critic.summary,
                    needs_rewrite=True,
                    raw_response="",
                )
            )
            try:
                rewrite_one(
                    conn, content,
                    critique_text=critique_text,
                    now=now,
                )
            except CreateError as e:
                _mark_failed(conn, content.id, f"rewrite: {e}")
                return ContentGateOutcome(
                    content_id=content.id,
                    final_status=ContentStatus.FAILED.value,
                    score_total=None, score=None, critique=last_critic,
                    rewrites=rewrites, decision=None,
                    reason=f"rewrite: {e}",
                )
            rewrites += 1
            canonical_after_rewrite = _read_canonical(content)
            try:
                critic2 = critique_one(
                    title=content.title,
                    canonical_md=canonical_after_rewrite,
                    conn=conn,
                    ref_id=content.id,
                )
            except (GateError, CreateError) as e:
                _mark_failed(conn, content.id, f"re-critic: {e}")
                return ContentGateOutcome(
                    content_id=content.id,
                    final_status=ContentStatus.FAILED.value,
                    score_total=None, score=None, critique=last_critic,
                    rewrites=rewrites, decision=None,
                    reason=f"re-critic: {e}",
                )
            last_critic = critic2
            # 复评后如仍需重写 → 继续循环；否则跳出
            if not (last_critic.needs_rewrite and rewrites < gate_cfg.max_rewrites):
                break

    # 3. 独立评分（无论是否重写都跑）
    try:
        score = score_one(
            title=content.title,
            canonical_md=canonical_after_rewrite,
            anchors=anchors,
            conn=conn,
            ref_id=content.id,
        )
    except (GateError, CreateError) as e:
        _mark_failed(conn, content.id, f"scorer: {e}")
        return ContentGateOutcome(
            content_id=content.id,
            final_status=ContentStatus.FAILED.value,
            score_total=None, score=None, critique=last_critic,
            rewrites=rewrites, decision=None,
            reason=f"scorer: {e}",
        )

    # 4. 判定 gated / discarded
    decision = decide_gate(
        info=score.info, fun=score.fun, view=score.view,
        problems=score.problems,
        threshold_total=gate_cfg.threshold_total,
        threshold_each=gate_cfg.threshold_each,
        verdict=score.verdict,
    )

    # 5. 落 critique.md
    reason_parts = []
    if last_critic.problems:
        reason_parts.append(f"critic: {len(last_critic.problems)} 问题")
    if rewrites:
        reason_parts.append(f"重写 {rewrites} 轮")
    reason_parts.append(f"score {decision.score}/{30}")
    reason = "；".join(reason_parts)
    _write_critique_md(content_dir, last_critic, score, decision, rewrites, reason)

    # 6. 原子事务：分数写入 + 状态转移（审计 Bug 2）
    final_status = (
        ContentStatus.GATED.value
        if decision.allow_publish
        else ContentStatus.DISCARDED.value
    )
    with conn:
        _save_gate_scores_no_commit(conn, content, decision, now)
        db_mod.transition(
            conn, "contents", content.id,
            from_status=ContentStatus.DRAFT.value,
            to_status=final_status,
        )

    return ContentGateOutcome(
        content_id=content.id,
        final_status=final_status,
        score_total=decision.score,
        score=score, critique=last_critic,
        rewrites=rewrites, decision=decision,
        reason=reason,
    )


# ── 公开入口 ────────────────────────────────────────────────

def run_gate(
    conn: sqlite3.Connection,
    *,
    gate_cfg: GateConfig,
    anchors_dir: Path | None = None,
    now: str | None = None,
) -> GateRunResult:
    """运行门禁编排。

    Args:
        conn: SQLite 连接
        gate_cfg: config.gate 段（threshold_total/each/max_rewrites）
        anchors_dir: 自定义锚点目录（默认 pipeline/gate/anchors/）
        now: ISO8601 UTC（测试可固定）

    Returns:
        GateRunResult

    Raises:
        BudgetExceeded: 系统性（LLM 月度预算超限）
        StaleState: 系统性（并发改 status）
        IllegalTransition: 系统性（契约被破坏）
    """
    now = now or db_mod.now_utc()
    anchors = load_anchors(anchors_dir)
    if len(anchors.anchors) == 0:
        raise GateError(
            "no anchors loaded; check pipeline/gate/anchors/ "
            "(need 6 .md + .json pairs)"
        )

    drafts = db_mod.get_contents_by_status(conn, ContentStatus.DRAFT.value)
    outcomes: list[ContentGateOutcome] = []

    for content in drafts:
        outcome = _process_one(
            conn, content,
            anchors=anchors,
            gate_cfg=gate_cfg,
            now=now,
        )
        outcomes.append(outcome)

    gated = sum(
        1 for o in outcomes if o.final_status == ContentStatus.GATED.value
    )
    discarded = sum(
        1 for o in outcomes if o.final_status == ContentStatus.DISCARDED.value
    )
    failed = sum(
        1 for o in outcomes if o.final_status == ContentStatus.FAILED.value
    )
    return GateRunResult(
        outcomes=tuple(outcomes),
        gated_count=gated,
        discarded_count=discarded,
        failed_count=failed,
    )