"""AI 语义主题去重（防"同事件多角度报道"占满 daily_quota）。

借鉴 Horizon（Thysrael/Horizon, MIT License, commit 3e21c04）：
  - src/ai/prompts.py: TOPIC_DEDUP_SYSTEM / TOPIC_DEDUP_USER
  - src/orchestrator.py::merge_topic_duplicates（asyncio, 433-504 行）

设计要点（搬运 + 适配）：
  - 单次 LLM 调用让模型识别"同事件不同 URL/不同标题"的条目组
  - 每组 dup 中第一个 idx 作 primary（代表），其余作 duplicate
  - Horizon 要求 items 已按 ai_score 降序排序（让 primary 总是高分）；
    本系统 M1-7 在 score 之前调，items 未排序——primary 退化为"输入顺序
    第一个"，仍能正确合并，代价是代表条不一定是最高分的（best-effort）。
    彻底解决需要"先 score 再 dedup"，与 M1-6 URL 去重的"先 dedup 再 score"
    顺序相反（语义去重成本高，逻辑上应在 score 后；但 score 已耗时在前
    阶段就先做了 URL 去重）。本 task 保持 URL dedup → 语义 dedup → score
    的约定，避免改造下游接口；代表条选择偏差由 selector 后续 top N 兜底。

契约（不变）：
  - 不动 SourceAdapter / RawItem / TECH_SPEC §3 schema / models.Topic
  - 纯函数模块，不接触 DB，不接触网络
  - 失败静默 fallback（best-effort，不阻塞 score 主流程）

已知限制（保留到下一个 task）：
  - in-memory 合并；DB 中重复条目下次 cron score 仍会再次合并
    （少量 LLM 浪费，与 M1-6 同模式）
  - items 未按 score 排序 → primary 不一定是最高分（见上文）
  - 彻底解决需 topics 表加 `merged_into_topic_id` 字段（动契约，TODO）

合并时机：score 编排层（`runner.py::score_all`），URL dedup 之后、
score_topic 之前；代表条进 score，duplicate 不参与评分（避免同主题
多次占 daily_quota）。
"""
from __future__ import annotations

import json
from typing import Any, Callable

from pipeline.creators import llm as llm_mod
from pipeline.models import Topic
from pipeline.utils.log import get_logger, log_event


_LOGGER = get_logger("pipeline.topics.topic_dedup", "logs")


# ── Prompts（移植自 Horizon，MIT License）────────────────────
#
# Source: https://github.com/Thysrael/Horizon src/ai/prompts.py
# License: MIT（Copyright (c) 2026 Thysrael）
# Pin: commit 3e21c04 (HEAD of master at evaluation time)
# Original prompt text kept verbatim; adaptation in this file is the
# items-text builder (_build_items_text) and the output post-processor
# (best-effort fallback, log warning, in-memory merge).
#
# 翻译/调整说明：
#   - TOPIC_DEDUP_SYSTEM / TOPIC_DEDUP_USER 原文不动；
#   - items 段由 _build_items_text 构造（Horizon 用 ContentItem，
#     本系统用 Topic dataclass；字段名映射 title→title,
#     ai_tags→"—"（无该字段）, ai_summary→summary）。
#   - 输出 JSON schema 不变：{"duplicates": [[primary_idx, dup_idx, ...], ...]}

TOPIC_DEDUP_SYSTEM = """You are a news deduplication assistant. Identify groups of news items that cover the exact same real-world event, release, or announcement.

Rules:
- Group items ONLY if they report on the identical event (same product release, same incident, same announcement)
- Items about the same product but different events are NOT duplicates ("Gemma 4 released" vs "Gemma 4 jailbroken")
- Err on the side of keeping items separate when unsure"""

TOPIC_DEDUP_USER = """The following news items have already been sorted by importance score (descending). Identify which items are duplicates of each other.

{items}

Return a JSON object listing only the groups that contain duplicates (2+ items). Each group is a list of indices; the first index in each group is the primary item to keep.

Respond with valid JSON only:
{{
  "duplicates": [[<primary_idx>, <dup_idx>, ...], ...]
}}

If there are no duplicates at all, return: {{"duplicates": []}}"""


# ── 私有 helper ──────────────────────────────────────────

def _build_items_text(items: list[Topic]) -> str:
    """构造 prompt 的 items 段（每条 [idx] title + Tags + Summary）。

    与 Horizon 一致：tags 缺省为 "—"，summary 缺省为 "—"。
    Topic 没有 ai_tags 字段（不需要），tags 恒为 "—"；summary 字段
    可能为 None 或空串，统一映射为 "—"。
    """
    lines: list[str] = []
    for i, t in enumerate(items):
        tags = "—"  # Topic 模型无 ai_tags
        summary = (t.summary or "").strip() or "—"
        lines.append(f"[{i}] {t.title}\n    Tags: {tags}\n    Summary: {summary}")
    return "\n\n".join(lines)


def _parse_response(text: str) -> dict[str, Any]:
    """parse 回调：验证 LLM 返回结构合法。

    Horizon 的 parse_json_response 在解析失败时返回 None（静默）；
    本系统的 complete_json 协议要求失败时抛 ValueError 以触发
    自动 retry，所以这里抛错而不是返回 None。

    Raises:
        json.JSONDecodeError: text 不是合法 JSON
        ValueError: JSON 不是 dict / 缺 'duplicates' 字段
    """
    obj = json.loads(text)
    if not isinstance(obj, dict):
        raise ValueError(
            f"dedup response must be a dict, got {type(obj).__name__}"
        )
    if "duplicates" not in obj:
        raise ValueError(
            f"dedup response missing 'duplicates' key: keys={list(obj.keys())}"
        )
    return obj


def _compute_drop_indices(
    duplicate_groups: Any, n: int
) -> set[int]:
    """从 LLM 返回的 groups 中提取要 drop 的 idx 集合。

    严格校验：idx 必须是 int、≥ 0、< n；primary idx 与 dup idx 不同；
    每组 ≥ 2 个 idx。任何不合法的组 → 跳过（保守不乱删）。
    primary idx 重复出现 → 只取第一次（避免一个 primary 同时被当两个
    组的代表而自相矛盾）。
    """
    if not isinstance(duplicate_groups, list):
        return set()

    drop: set[int] = set()
    used_primary: set[int] = set()
    for group in duplicate_groups:
        if not isinstance(group, list) or len(group) < 2:
            continue
        # 第一个是 primary
        primary_idx = group[0]
        if (
            not isinstance(primary_idx, int)
            or primary_idx < 0
            or primary_idx >= n
        ):
            continue
        if primary_idx in used_primary:
            continue  # 已被别的组占用为 primary
        used_primary.add(primary_idx)
        for dup_idx in group[1:]:
            if (
                not isinstance(dup_idx, int)
                or dup_idx < 0
                or dup_idx >= n
            ):
                continue
            if dup_idx == primary_idx:
                continue  # 自我引用 skip
            drop.add(dup_idx)
    return drop


# ── 公开 API ────────────────────────────────────────────

def dedup_topics(
    items: list[Topic],
    *,
    ai_client: Callable[..., Any] | None = None,
) -> tuple[list[Topic], list[Topic]]:
    """AI 语义去重（识别"同事件不同 URL/不同标题"的条目）。

    Args:
        items: 待去重 topics
        ai_client: 可选 LLM 调用入口（duck-typed: callable 与
                   `pipeline.creators.llm.complete_json` 签名兼容）。
                   传 None 时使用 module-level provider（CLI 默认路径，
                   测试用 set_provider 注入 mock）。

    Returns:
        (representatives, duplicates):
          - representatives: 保留的 topics（每组 dup 的代表条 + 不重复的）
          - duplicates: 被合并掉的 topics
          - 输入顺序保留

    失败语义（best-effort，不抛、不阻塞 score 主流程）：
      - LLM 异常 / JSON 解析失败 / 返回结构非法 → 返回 (items, [])
      - 组结构异常（单元素、idx 越界、idx 非 int）→ 跳过该组
      - log warning（stage='score_dedup', ref_id=None）
    """
    if not items:
        return [], []
    if len(items) == 1:
        # 1 条无分组意义；省一次 LLM 调用
        return list(items), []

    items_text = _build_items_text(items)
    user_prompt = TOPIC_DEDUP_USER.format(items=items_text)

    try:
        if ai_client is not None:
            parsed = ai_client(
                user_prompt,
                stage="score_dedup",
                parse=_parse_response,
                model_tier="cheap",
                max_tokens=4096,
                max_retries=1,
            )
        else:
            parsed = llm_mod.complete_json(
                user_prompt,
                stage="score_dedup",
                parse=_parse_response,
                model_tier="cheap",
                max_tokens=4096,
                max_retries=1,
            )
    except Exception as e:
        # best-effort：失败静默 + log warning，不阻塞 score 主流程
        # （HARD_PARTS §5 "失败静默 + 重新尝试" 类似语义）
        log_event(
            _LOGGER,
            30,  # logging.WARNING
            f"M1-7 semantic dedup failed; fallback to no-dedup: "
            f"{type(e).__name__}: {e}",
            stage="score_dedup",
            ref_id=None,
        )
        return list(items), []

    duplicate_groups = parsed.get("duplicates", [])
    drop_indices = _compute_drop_indices(duplicate_groups, len(items))

    reps: list[Topic] = []
    dups: list[Topic] = []
    for i, item in enumerate(items):
        if i in drop_indices:
            dups.append(item)
        else:
            reps.append(item)
    return reps, dups
