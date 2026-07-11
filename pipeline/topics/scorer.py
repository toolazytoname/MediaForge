"""选题评分（M1-4 + HARD_PARTS §4 用 cheap 档控本）。

每条 raw topic → LLM 评分 → JSON 解析 → 写入 pillar/score/reason + 状态转移。

行为契约：
  - LLM 走 cheap 档（Haiku）
  - JSON 解析失败 → 重试 1 次；仍失败 → 状态转 rejected（不阻塞其他 topic）
  - pillar 必须命中 config.pillars；score 必须在 [0, 10]——任一不满足视为解析失败
  - 评分落库 + raw→scored（或 raw→rejected）同一事务（HARD_PARTS §5）
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from pipeline import db
from pipeline.config import Pillar
from pipeline.creators import llm as llm_mod
from pipeline.models import Topic, TopicStatus


@dataclass(frozen=True)
class ScoreResult:
    """单条 topic 的评分结果。accepted=False 表示 JSON 解析失败，状态已转 rejected。"""
    pillar: str | None
    score: float | None
    reason: str | None
    accepted: bool


# ── prompt 模板 ────────────────────────────────────────
# 简洁可读，便于后续迁移到 prompts/score.md（M2-1 起各阶段 prompt 独立文件）

_SCORE_PROMPT = """你是 MediaForge 的选题评分助手。根据下面候选选题与内容支柱清单，给出该选题的归属支柱与 0-10 分评分。

【选题】
标题：{title}
摘要：{summary}
URL：{url}

【内容支柱】
{pillars_block}

【要求】
1. 选择最匹配的一个 pillar id（必须是上面列出的 id 之一）
2. 评分 0-10 的浮点数（带一位小数），依据该选题与对应支柱描述/评分提示的契合度
3. 用一句话（≤50 字中文）说明评分理由

【输出格式（严格 JSON，禁止多余文本）】
{{"pillar": "<id>", "score": <float>, "reason": "<一句话>"}}
"""


def _render_pillars(pillars: list[Pillar]) -> str:
    lines = []
    for p in pillars:
        lines.append(
            f"- id={p.id} | {p.name} | 描述：{p.description} | 提示：{p.scoring_hint}"
        )
    return "\n".join(lines)


def _build_prompt(topic: Topic, pillars: list[Pillar]) -> str:
    return _SCORE_PROMPT.format(
        title=topic.title,
        summary=topic.summary or "（无）",
        url=topic.url or "（无）",
        pillars_block=_render_pillars(pillars),
    )


# ── JSON 解析 + 校验 ──────────────────────────────────

def _parse_response(
    text: str, *, known_pillar_ids: set[str]
) -> tuple[str, float, str] | None:
    """解析 LLM 返回的 JSON。返回 None 表示应触发重试。

    校验规则：
      - 必须能 json.loads
      - 必须含 pillar / score / reason 三个键
      - pillar 必须在 known_pillar_ids 中
      - score 是 [0, 10] 的有限浮点
    """
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None

    pillar = obj.get("pillar")
    score = obj.get("score")
    reason = obj.get("reason")
    if not isinstance(pillar, str) or pillar not in known_pillar_ids:
        return None
    if not isinstance(score, (int, float)):
        return None
    if not (0.0 <= float(score) <= 10.0):
        return None
    if not isinstance(reason, str):
        return None
    return pillar, float(score), reason


# ── 状态机转移 + 字段写入 ─────────────────────────────

def _apply_score(
    conn: sqlite3.Connection,
    topic: Topic,
    *,
    pillar: str | None,
    score: float | None,
    reason: str | None,
    now: str,
    accepted: bool,
) -> None:
    """更新 topic 字段 + 状态转移（同一事务）。"""
    new_status = (
        TopicStatus.SCORED.value if accepted
        else TopicStatus.REJECTED.value
    )

    with conn:
        # 写字段
        conn.execute(
            """
            UPDATE topics
            SET pillar=?, score=?, score_reason=?, status=?, updated_at=?
            WHERE id=? AND status=?
            """,
            (
                pillar, score, reason, new_status, now,
                topic.id, TopicStatus.RAW.value,
            ),
        )


# ── 公开入口 ─────────────────────────────────────────

def score_topic(
    conn: sqlite3.Connection,
    topic: Topic,
    *,
    pillars: list[Pillar],
    now: str,
) -> ScoreResult:
    """评一条 topic。返回 ScoreResult。

    异常：
      - LLM 抛非 RetryableError 上抛（异常即整批失败，由编排层决定是否跳过）
      - LLM 抛 RetryableError：重试在 llm.complete 内部已做完；到这里还没成功
        表示穷尽性失败，由本函数捕获并转 rejected
    """
    prompt = _build_prompt(topic, pillars)
    pillar_ids = {p.id for p in pillars}

    last_text: str | None = None
    for _attempt in (1, 2):  # 首次 + 1 次重试
        try:
            text = llm_mod.complete(
                prompt,
                stage="score",
                ref_id=topic.id,
                model_tier="cheap",
                max_tokens=2048,
                conn=conn,
            )
        except llm_mod.RetryableError:
            # 重试用尽——视为拒绝
            _apply_score(
                conn, topic, pillar=None, score=None, reason=None,
                now=now, accepted=False,
            )
            return ScoreResult(None, None, None, False)

        last_text = text
        parsed = _parse_response(text, known_pillar_ids=pillar_ids)
        if parsed is not None:
            pillar, score, reason = parsed
            _apply_score(
                conn, topic, pillar=pillar, score=score, reason=reason,
                now=now, accepted=True,
            )
            return ScoreResult(pillar, score, reason, True)

    # 两次都解析失败 → reject
    _apply_score(
        conn, topic, pillar=None, score=None, reason=None,
        now=now, accepted=False,
    )
    return ScoreResult(None, None, None, False)