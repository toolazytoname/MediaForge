"""批判轮（HARD_PARTS §3 隔离 + TrendPublish 修订协议第一步）。

职责：
  1. 独立 LLM 会话（不带创作过程上下文）只审稿不重写
  2. 强制 JSON 输出（problems + summary）
  3. 区分可重写问题 vs 不可重写问题（事实/风险类）
  4. 决策：触发重写 / 直接 discarded

JSON 解析失败 → 单条 content 标记 failed（raise GateError 让编排层捕获）。
LLM 异常（RetryableError 耗尽）→ 同样 raise GateError。
BudgetExceeded → 上抛不吞（与 create 编排层一致）。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from pipeline.creators import llm as llm_mod
from pipeline.creators.llm import complete
from pipeline.gate.decision import (
    Problem,
    has_blocker,
    safe_to_revise,
)
from pipeline.utils.errors import GateError


# ── prompts ────────────────────────────────────────────────

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_CRITIC_PROMPT_PATH = _PROMPTS_DIR / "critic.md"


def _render_critic_prompt(*, title: str, canonical_md: str) -> str:
    tpl = _CRITIC_PROMPT_PATH.read_text(encoding="utf-8")
    return tpl.format(title=title, canonical_md=canonical_md)


# ── 数据类 ─────────────────────────────────────────────────

@dataclass(frozen=True)
class CritiqueResult:
    """批判轮结果。"""
    problems: tuple[Problem, ...]
    summary: str
    needs_rewrite: bool    # True = 有可重写问题，建议触发重写
    raw_response: str      # LLM 原始响应（落 critique.md 用）


# ── 解析 ────────────────────────────────────────────────────

_VALID_CATEGORIES = frozenset(
    {"fact", "title", "structure", "tone", "html", "image", "risk"}
)
_VALID_SEVERITIES = frozenset({"low", "medium", "high", "blocker"})


def _parse_problem(obj: dict) -> Problem:
    if not isinstance(obj, dict):
        raise GateError(f"problem not a dict: {obj!r}")
    category = obj.get("category", "")
    if category not in _VALID_CATEGORIES:
        raise GateError(f"problem category invalid: {category!r}")
    severity = obj.get("severity", "")
    if severity not in _VALID_SEVERITIES:
        raise GateError(f"problem severity invalid: {severity!r}")
    return Problem(
        category=category,
        severity=severity,
        message=str(obj.get("message", "")),
        evidence=str(obj.get("evidence", "")),
        suggestion=str(obj.get("suggestion", "")),
        auto_fixable=bool(obj.get("autoFixable", False)),
    )


def _parse_critique(text: str) -> tuple[tuple[Problem, ...], str]:
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        raise GateError(f"critic JSON parse failed: {e}") from e
    if not isinstance(obj, dict):
        raise GateError(f"critic response not a dict: {text!r}")
    problems_raw = obj.get("problems")
    if problems_raw is None:
        raise GateError(f"critic response missing 'problems' field: {text!r}")
    if not isinstance(problems_raw, list):
        raise GateError(f"critic problems not a list: {problems_raw!r}")
    problems = tuple(_parse_problem(p) for p in problems_raw)
    summary = str(obj.get("summary", ""))
    return problems, summary


# ── 公开入口 ────────────────────────────────────────────────

def critique_one(
    *,
    title: str,
    canonical_md: str,
    conn: sqlite3.Connection | None = None,
    ref_id: str | None = None,
) -> CritiqueResult:
    """对单篇 canonical 长文做批判。

    Args:
        title: 文章标题（仅用于审稿上下文；不影响打分）
        canonical_md: 完整 markdown 正文
        conn: DB 连接（llm.complete 写 llm_calls 用）
        ref_id: content_id，用于日志关联

    Returns:
        CritiqueResult
          - problems: 问题列表
          - summary: 一句话总结
          - needs_rewrite: True 表示有可重写问题，建议走 rewrite 流程

    Raises:
        GateError: JSON 解析失败 / 字段缺失（结构性错误，编排层标记 failed）
    """
    prompt = _render_critic_prompt(title=title, canonical_md=canonical_md)
    try:
        text = complete(
            prompt,
            stage="gate_critic",
            ref_id=ref_id,
            model_tier="creative",
            max_tokens=2048,
            conn=conn,
        )
    except llm_mod.RetryableError as e:
        raise GateError(
            f"critic LLM retry exhausted for ref={ref_id}: {e}"
        ) from e

    problems, summary = _parse_critique(text)
    needs_rewrite = bool(problems) and safe_to_revise(problems)
    return CritiqueResult(
        problems=problems,
        summary=summary,
        needs_rewrite=needs_rewrite,
        raw_response=text,
    )


def render_critique_text(result: CritiqueResult) -> str:
    """将 CritiqueResult 渲染为可落盘的 markdown 文本（critique.md）。"""
    lines: list[str] = []
    lines.append("# Critic 审稿意见")
    lines.append("")
    lines.append(f"**总结**：{result.summary or '（无）'}")
    lines.append("")
    if not result.problems:
        lines.append("无重大问题。")
        return "\n".join(lines)
    lines.append("## 问题列表")
    lines.append("")
    lines.append("| # | 类别 | 严重度 | 问题 | 证据 | 建议 | 可自动修 |")
    lines.append("|---|------|--------|------|------|------|----------|")
    for i, p in enumerate(result.problems, 1):
        lines.append(
            f"| {i} | {p.category} | {p.severity} | {p.message} | "
            f"{p.evidence} | {p.suggestion} | "
            f"{'✓' if p.auto_fixable else ''} |"
        )
    lines.append("")
    lines.append(f"**has_blocker**: {has_blocker(result.problems)}")
    lines.append(f"**safe_to_revise**: {safe_to_revise(result.problems)}")
    lines.append(f"**needs_rewrite**: {result.needs_rewrite}")
    return "\n".join(lines)