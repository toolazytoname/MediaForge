"""口播稿派生（TECH_SPEC §5.6 + HARD_PARTS §6）。

canonical 长文 → LLM 派生 60-90s 口播稿 + 关键词。
- 不让 MPT 自己写文案（HARD_PARTS §6 决策 2）
- prompt 移植 M2-1 防幻觉条款（不写未明确陈述的商业状态/定价）
- 解析失败 → 抛 CreateError 让编排层记 failed

prompt 文件：`pipeline/creators/prompts/video_script.md`（便于迭代）。
"""
from __future__ import annotations

import json
from pathlib import Path

from pipeline.creators import llm as llm_mod
from pipeline.utils.errors import CreateError


_PROMPT_PATH = (
    Path(__file__).parent / "prompts" / "video_script.md"
)


def _load_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


# ── 派生入口 ──────────────────────────────────────────


def derive_video_script(
    canonical_content: str,
    *,
    ref_id: str | None = None,
    stage: str = "video_script",
    model_tier: str = "creative",
    prompt_path: Path | None = None,
) -> dict:
    """canonical → {script, keywords, duration_s, hook_score}。

    Args:
        canonical_content: 全文 markdown
        ref_id: 关联 content_id（审计）
        stage: 审计 stage 名
        model_tier: 'creative' 默认；cheap 会让口播稿质量塌
        prompt_path: 注入 prompt 路径（测试）

    Returns:
        dict: {"script": str, "keywords": list[str],
               "duration_s": int, "hook_score": int}

    Raises:
        CreateError: 解析失败 / 字段缺失 / 重试后仍坏
    """
    p_path = prompt_path or _PROMPT_PATH
    template = p_path.read_text(encoding="utf-8")
    prompt = template.replace("{canonical_content}", canonical_content)

    raw = llm_mod.complete_json(
        prompt,
        stage=stage,
        ref_id=ref_id,
        model_tier=model_tier,
        max_tokens=2048,
        parse=_parse_video_script,
    )
    return raw


def _parse_video_script(raw: dict) -> dict:
    """校验 LLM 返回的字段，缺一抛 CreateError。"""
    if not isinstance(raw, dict):
        raise CreateError(
            f"video script not dict: {type(raw).__name__}"
        )
    script = raw.get("script")
    if not isinstance(script, str) or not script.strip():
        raise CreateError("video script missing or empty")
    keywords = raw.get("keywords", [])
    if not isinstance(keywords, list) or not all(
        isinstance(k, str) for k in keywords
    ):
        raise CreateError(f"video keywords not list[str]: {keywords!r}")
    duration_s = raw.get("duration_s", 0)
    if not isinstance(duration_s, int) or not (30 <= duration_s <= 180):
        raise CreateError(
            f"video duration_s out of range: {duration_s}"
        )
    hook_score = raw.get("hook_score", 0)
    if not isinstance(hook_score, int) or not (1 <= hook_score <= 10):
        raise CreateError(
            f"video hook_score out of range: {hook_score}"
        )
    return {
        "script": script.strip(),
        "keywords": [k.strip() for k in keywords if k.strip()],
        "duration_s": duration_s,
        "hook_score": hook_score,
    }


__all__ = ["derive_video_script", "_parse_video_script"]