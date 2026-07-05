"""canonical 长文创作管道（M2-1）。

两段式：
  Stage 1 outline → LLM → JSON {viewpoint, outline: list[str]}
  Stage 2 essay   → LLM → Markdown 正文（1500-3000 字）

幂等保证（HARD_PARTS §5）：
  - 输出写到 output/<date>/<content_id>.tmp/，成功后 rename 为最终目录
  - 重跑时发现 .tmp 残留 → 先删 .tmp 再来

异常：
  - URL 抓取失败 → fallback 到 title+summary（不抛）
  - LLM 异常或 stage1 JSON 解析失败 → CreateError 上抛，topic 状态不动
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from pipeline import db
from pipeline.config import Pillar
from pipeline.creators import llm as llm_mod
from pipeline.creators import source_fetcher
from pipeline.creators.llm import complete
from pipeline.models import Content, ContentStatus, Topic, TopicStatus
from pipeline.utils.errors import CreateError
from pipeline.utils.ids import new_id

# 默认输出根目录（CLI 可覆盖）
DEFAULT_OUTPUT_ROOT = Path("output")

# 提示词文件路径
_PROMPTS_DIR = Path(__file__).parent / "prompts"
_OUTLINE_PROMPT_PATH = _PROMPTS_DIR / "canonical_outline.md"
_ESSAY_PROMPT_PATH = _PROMPTS_DIR / "canonical_essay.md"


@dataclass(frozen=True)
class CreateResult:
    """单条 topic 创建结果。"""
    content: Content
    output_dir: Path


def _render_outline_prompt(
    topic: Topic, source_text: str | None
) -> str:
    tpl = _OUTLINE_PROMPT_PATH.read_text(encoding="utf-8")
    return tpl.format(
        title=topic.title,
        summary=topic.summary or "（无）",
        url=topic.url or "（无）",
        source_text=source_text or "（未抓到原文，仅基于标题与摘要）",
    )


def _render_essay_prompt(
    topic: Topic,
    viewpoint: str,
    outline: list[str],
    source_text: str | None,
) -> str:
    tpl = _ESSAY_PROMPT_PATH.read_text(encoding="utf-8")
    bullets = "\n".join(f"- {x}" for x in outline)
    return tpl.format(
        title=topic.title,
        url=topic.url or "（无）",
        viewpoint=viewpoint,
        outline_bullets=bullets,
        source_text=source_text or "（无）",
    )


def _parse_outline(text: str) -> tuple[str, list[str]]:
    """解析 stage1 JSON。失败抛 CreateError（由编排层捕获）。"""
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        raise CreateError(f"stage1 outline JSON parse failed: {e}") from e
    if not isinstance(obj, dict):
        raise CreateError(f"stage1 outline not a dict: {text!r}")
    viewpoint = obj.get("viewpoint")
    outline = obj.get("outline")
    if not isinstance(viewpoint, str) or not isinstance(outline, list):
        raise CreateError(
            f"stage1 outline missing fields: {text!r}"
        )
    if not all(isinstance(x, str) for x in outline):
        raise CreateError(f"stage1 outline items not str: {outline!r}")
    return viewpoint, outline


# ── 文件写入（tmp-rename 模式）──────────────────────

def _write_outputs(
    *,
    out_dir: Path,
    canonical_md: str,
    meta: dict,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "canonical.md").write_text(canonical_md, encoding="utf-8")
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── 公开入口 ──────────────────────────────────────

def create_one(
    conn: sqlite3.Connection,
    topic: Topic,
    *,
    pillars: list[Pillar],  # 备用：未来可按 pillar 调 prompt 模板
    output_root: Path | str = DEFAULT_OUTPUT_ROOT,
    now: str,
) -> Content:
    """为单条 selected topic 生成 canonical 长文。

    Returns:
        Content 不可变记录（已落库 status=draft）

    Raises:
        CreateError: LLM 异常 / outline 解析失败 / 写盘失败
    """
    output_root = Path(output_root)
    # 1. 取素材（URL 失败不抛）
    source_text = source_fetcher.fetch_text(topic.url)

    # 2. Stage 1: outline
    outline_prompt = _render_outline_prompt(topic, source_text)
    try:
        outline_text = complete(
            outline_prompt,
            stage="create_outline",
            ref_id=topic.id,
            model_tier="creative",
            max_tokens=1024,
            conn=conn,
        )
    except llm_mod.RetryableError as e:
        # 仅包裹瞬时网络/限流类失败 → 单条 skip
        # BudgetExceeded / ValueError 等系统性错误必须原样上抛
        raise CreateError(
            f"stage1 LLM retry exhausted for topic={topic.id}: {e}"
        ) from e

    viewpoint, outline = _parse_outline(outline_text)

    # 3. Stage 2: essay
    essay_prompt = _render_essay_prompt(
        topic, viewpoint, outline, source_text
    )
    try:
        essay = complete(
            essay_prompt,
            stage="create_essay",
            ref_id=topic.id,
            model_tier="creative",
            max_tokens=6144,  # 3000 字中文 + 富余（每 token ≈ 1.5-2 中文字）
            conn=conn,
        )
    except llm_mod.RetryableError as e:
        raise CreateError(
            f"stage2 LLM retry exhausted for topic={topic.id}: {e}"
        ) from e

    # 4. 准备输出目录（HARD_PARTS §5 tmp→rename）
    content_id = new_id("c")
    date_str = now[:10]  # YYYY-MM-DD
    final_dir = output_root / date_str / content_id
    tmp_dir = final_dir.with_name(final_dir.name + ".tmp")

    # 清残留 .tmp（HARD_PARTS §5 幂等）
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)

    meta = {
        "content_id": content_id,
        "topic_id": topic.id,
        "title": topic.title,
        "url": topic.url,
        "pillar": topic.pillar,
        "viewpoint": viewpoint,
        "outline": outline,
        "source_text_chars": len(source_text) if source_text else 0,
        "created_at": now,
    }
    _write_outputs(out_dir=tmp_dir, canonical_md=essay, meta=meta)
    tmp_dir.rename(final_dir)  # atomic rename

    # 5. contents 表 + topic 转移（同一事务）
    canonical_path = str(final_dir / "canonical.md")
    with conn:
        conn.execute(
            """
            INSERT INTO contents
                (id, topic_id, pillar, title, canonical_path, formats,
                 gate_score_total, gate_scores, gate_verdict,
                 status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                content_id, topic.id, topic.pillar or "uncategorized",
                topic.title, canonical_path, "[]",
                None, None, None,
                ContentStatus.DRAFT.value, now, now,
            ),
        )
        # 状态机转移 selected → consumed
        db.transition(
            conn, "topics", topic.id,
            from_status=TopicStatus.SELECTED.value,
            to_status=TopicStatus.CONSUMED.value,
        )

    # 6. 返回 Content
    return Content(
        id=content_id,
        topic_id=topic.id,
        pillar=topic.pillar or "uncategorized",
        title=topic.title,
        canonical_path=canonical_path,
        formats=(),
        gate_score_total=None,
        gate_scores=None,
        gate_verdict=None,
        status=ContentStatus.DRAFT.value,
        created_at=now,
        updated_at=now,
    )