"""独立评分会话（HARD_PARTS §3 隔离 + TrendPublish 7 维→MediaForge 3 维映射）。

设计原则：
  - 与创作管道完全隔离：不带创作 prompt/上下文，只给最终 markdown
  - 锚点对比：prompt 里嵌入 6 篇校准样例（good/mid/bad 各 2 篇）
  - 强制 JSON：info/fun/view/problems/verdict 五字段
  - 防御评分自我偏袒：先列问题再打分（强制批判先行）
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from pipeline.creators import llm as llm_mod
from pipeline.creators.llm import complete
from pipeline.gate.anchors_loader import (
    AnchorsBundle,
    render_for_prompt,
)
from pipeline.gate.decision import Problem
from pipeline.utils.errors import GateError


# ── prompts ────────────────────────────────────────────────

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_SCORER_PROMPT_PATH = _PROMPTS_DIR / "scorer.md"


def _render_scorer_prompt(
    *,
    title: str,
    canonical_md: str,
    anchors: AnchorsBundle,
    excerpt_chars: int = 600,
) -> str:
    sections = render_for_prompt(anchors, excerpt_chars=excerpt_chars)
    tpl = _SCORER_PROMPT_PATH.read_text(encoding="utf-8")
    return tpl.format(
        title=title,
        canonical_md=canonical_md,
        good_anchors=sections["good"] or "（无 good 锚点）",
        mid_anchors=sections["mid"] or "（无 mid 锚点）",
        bad_anchors=sections["bad"] or "（无 bad 锚点）",
    )


# ── 数据类 ─────────────────────────────────────────────────

@dataclass(frozen=True)
class ScoreResult:
    """单次评分结果。"""
    info: int
    fun: int
    view: int
    problems: tuple[Problem, ...]
    verdict: str
    raw_response: str

    @property
    def total(self) -> int:
        return self.info + self.fun + self.view

    @property
    def scores(self) -> dict:
        return {"info": self.info, "fun": self.fun, "view": self.view}


# ── 解析 ────────────────────────────────────────────────────

_VALID_CATEGORIES = frozenset(
    {"fact", "title", "structure", "tone", "html", "image", "risk"}
)
_VALID_SEVERITIES = frozenset({"low", "medium", "high", "blocker"})


def _parse_problem(obj: dict, *, require_evidence: bool = False) -> Problem:
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


def _parse_score(text: str) -> ScoreResult:
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        raise GateError(f"scorer JSON parse failed: {e}") from e
    if not isinstance(obj, dict):
        raise GateError(f"scorer response not a dict: {text!r}")
    info = obj.get("info")
    fun = obj.get("fun")
    view = obj.get("view")
    # 严格 int（bool 是 int 子类，必须排除）
    def _is_score_int(x: object) -> bool:
        return isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= 10
    if not all(_is_score_int(x) for x in (info, fun, view)):
        raise GateError(
            f"scorer scores invalid (info/fun/view must be int 0-10): "
            f"info={info!r} fun={fun!r} view={view!r}"
        )
    problems_raw = obj.get("problems")
    if problems_raw is None:
        raise GateError(f"scorer response missing 'problems' field: {text!r}")
    if not isinstance(problems_raw, list):
        raise GateError(f"scorer problems not a list: {problems_raw!r}")
    problems = tuple(_parse_problem(p) for p in problems_raw)
    verdict = str(obj.get("verdict", ""))
    return ScoreResult(
        info=info, fun=fun, view=view,
        problems=problems, verdict=verdict,
        raw_response=text,
    )


# ── 公开入口 ────────────────────────────────────────────────

def score_one(
    *,
    title: str,
    canonical_md: str,
    anchors: AnchorsBundle,
    conn: sqlite3.Connection | None = None,
    ref_id: str | None = None,
    excerpt_chars: int = 600,
) -> ScoreResult:
    """对单篇 canonical 长文做独立评分。

    Args:
        title: 文章标题
        canonical_md: 完整 markdown 正文
        anchors: 加载好的 6 篇锚点
        conn: DB 连接
        ref_id: content_id，用于日志关联
        excerpt_chars: 锚点截取字符数（节省 prompt token）

    Returns:
        ScoreResult（info/fun/view/problems/verdict）

    Raises:
        GateError: JSON 解析失败 / 字段缺失 / 分数超界
    """
    prompt = _render_scorer_prompt(
        title=title,
        canonical_md=canonical_md,
        anchors=anchors,
        excerpt_chars=excerpt_chars,
    )
    try:
        text = complete(
            prompt,
            stage="gate_score",
            ref_id=ref_id,
            model_tier="critical",  # 评分走 critical 档（与创作隔离，HARD_PARTS §3）
            max_tokens=2048,
            conn=conn,
        )
    except llm_mod.RetryableError as e:
        raise GateError(
            f"scorer LLM retry exhausted for ref={ref_id}: {e}"
        ) from e

    return _parse_score(text)