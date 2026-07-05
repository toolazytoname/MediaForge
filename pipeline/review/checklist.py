"""M2-5 — REVIEW.md 生成（ARCHITECTURE §3.5 + TASKS M2-5）。

输入：contents 表中 status='gated' 的全部记录
输出：output/<date>/REVIEW.md —— 每条 gated 内容一节，含：
  - 标题 / pillar / 门禁总分 / 三维分 / 评语
  - canonical.md 相对路径（人在 REVIEW.md 旁打开就能预览）
  - 派生封面图相对路径（若存在，xiaohongshu/cards/cover.png）
  - "- [ ] approve" 复选框（人勾 [x]）
  - "- [-] reject: <理由>" 行（人写理由）

幂等：覆盖写。同一 date 反复跑结果一致（HARD_PARTS §5）。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from pipeline.db import get_contents_by_status
from pipeline.models import ContentStatus


# ── 路径工具 ────────────────────────────────────────────────


def checklist_path(output_root: Path | str, now_iso: str) -> Path:
    """output/<YYYY-MM-DD>/REVIEW.md，date 取 ISO 串前 10 字符。"""
    return Path(output_root) / now_iso[:10] / "REVIEW.md"


# ── 主入口 ─────────────────────────────────────────────────


def build_checklist_markdown(
    conn: sqlite3.Connection,
    *,
    date_str: str,
    output_root: Path | str,
) -> str:
    """读取 gated 内容 → 生成完整 REVIEW.md 文本（不落盘）。

    输出根 output_root 仅用于解析封面图实际路径——文本中所有路径都是
    相对于 REVIEW.md 所在目录（即 output/<date>/），方便人直接打开。
    """
    output_root = Path(output_root)
    review_dir = output_root / date_str

    gated = sorted(
        get_contents_by_status(conn, ContentStatus.GATED.value),
        key=lambda c: (c.gate_score_total or 0.0),
        reverse=True,
    )

    blocks: list[str] = []
    for c in gated:
        blocks.append(_render_block(c, review_dir))

    header = (
        f"# 审核清单 — {date_str}\n\n"
        f"共 {len(gated)} 条待审。\n\n"
        "---\n"
    )
    if not gated:
        return header + "\n_今日无待审内容。_\n"

    return header + "\n" + "\n\n".join(blocks) + "\n"


def write_checklist(
    conn: sqlite3.Connection,
    *,
    date_str: str,
    output_root: Path | str,
    now_iso: str,
) -> Path:
    """生成 REVIEW.md 并落盘到 output/<date>/REVIEW.md。

    返回写入的路径。父目录不存在则创建。
    """
    md = build_checklist_markdown(
        conn, date_str=date_str, output_root=output_root
    )
    path = checklist_path(output_root, now_iso)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 写临时文件再 rename（防半写状态，HARD_PARTS §5）
    tmp = path.with_name(path.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    tmp.write_text(md, encoding="utf-8")
    tmp.replace(path)
    return path


# ── 块渲染 ─────────────────────────────────────────────────


def _render_block(c, review_dir: Path) -> str:
    """单条 gated content 渲染为 REVIEW.md 一节。"""
    scores = _parse_gate_scores(c.gate_scores)
    score_text = (
        f"{c.gate_score_total:g}" if c.gate_score_total is not None else "—"
    )
    parts_text = ", ".join(
        f"{k}:{v}" for k, v in scores.items()
    ) if scores else "—"

    # canonical 路径：从 c.canonical_path（绝对/相对）抽出 content_id 之后的相对段
    # canonical_path 形如 output/2026-07-05/c_xxx/canonical.md
    canonical_rel = _relative_to_review(c.canonical_path, review_dir)
    cover_rel = _find_cover(c.canonical_path, review_dir)
    verdict = c.gate_verdict or "—"
    # 标题中可能含 ']'、换行 → 转义
    safe_title = c.title.replace("\n", " ").strip()

    lines = [
        f"## [{c.id}] {safe_title}",
        f"- pillar: {c.pillar}",
        f"- gate_score_total: {score_text} ({parts_text})",
        f"- gate_verdict: {verdict}",
        f"- canonical: {canonical_rel}",
    ]
    if cover_rel is not None:
        lines.append(f"- cover_image: {cover_rel}")
    lines.append("- [ ] approve")
    lines.append("- [-] reject:")
    return "\n".join(lines)


def _relative_to_review(
    canonical_path: str, review_dir: Path
) -> str:
    """把 canonical 路径换算成相对 REVIEW.md 的路径。

    约定（ARCHITECTURE §8 + canonical.py 写入契约）：
      canonical_path = '<output_root>/<date>/<cid>/canonical.md'
      REVIEW.md      = '<output_root>/<date>/REVIEW.md'
    → 相对路径恒为 './<cid>/canonical.md'（最后两段）
    """
    p = Path(canonical_path)
    if len(p.parts) < 2:
        return canonical_path
    return "./" + p.parent.name + "/" + p.name


def _find_cover(canonical_path: str, review_dir: Path) -> str | None:
    """在 content 目录下查找 xiaohongshu/cards/cover.png，命中则返回相对路径。"""
    p = Path(canonical_path)
    # canonical_path 是 'output/<date>/<cid>/canonical.md'，父目录 = content 目录
    content_dir = p.parent
    candidates = [
        content_dir / "xiaohongshu" / "cards" / "cover.png",
        content_dir / "xiaohongshu" / "cards" / "001.png",  # M2-4 命名约定
    ]
    for cand in candidates:
        if cand.exists():
            # 相对路径基于 review_dir
            try:
                rel = cand.relative_to(review_dir)
                return "./" + str(rel)
            except ValueError:
                return str(cand)
    return None


def _parse_gate_scores(gate_scores: str | None) -> dict:
    if not gate_scores:
        return {}
    try:
        obj = json.loads(gate_scores)
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(obj, dict):
        return {}
    return {k: v for k, v in obj.items() if isinstance(v, (int, float))}