"""M10-3 webui dataclass → JSON 序列化层。

设计目标：
  - 统一把 frozen dataclass（Topic / Content / Publication / Metric）转 dict，
    让 FastAPI 默认 JSONResponse 能直接 serialize
  - tuple 字段（formats / inline_images）转 list（JSON 不支持 tuple）
  - 已知敏感 / 噪声字段（如 Metric.raw 的平台原始 JSON）按需丢弃
  - 文件系统相关（list_content_files / content_image_urls）只读探测，
    目录不存在返回空
  - 写接口 write_canonical_jailed P1 留底不接路由——为 P2 canonical 在线
    编辑铺路

设计要点：
  - 函数纯（除文件枚举）——同输入同输出，便于单测
  - 字段 1:1，不发明字段、不改键名
  - dataclass field 顺序保持稳定（dict 序列化顺序在 Python 3.7+ 保证）
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from pipeline.models import (
    Content,
    Metric,
    Publication,
    Topic,
)


# ── dataclass → dict ──────────────────────────────────────


def topic_dict(t: Topic) -> dict[str, Any]:
    """Topic → dict（字段 1:1）。"""
    return {
        "id": t.id,
        "source": t.source,
        "title": t.title,
        "url": t.url,
        "summary": t.summary,
        "content_hash": t.content_hash,
        "pillar": t.pillar,
        "score": t.score,
        "score_reason": t.score_reason,
        "status": t.status,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
    }


def content_dict(c: Content) -> dict[str, Any]:
    """Content → dict。

    formats / inline_images 是 tuple，JSON 不支持——转 list。
    gate_scores 是 dict|None——None 时输出 null（JSON 标准）。
    """
    return {
        "id": c.id,
        "topic_id": c.topic_id,
        "pillar": c.pillar,
        "title": c.title,
        "canonical_path": c.canonical_path,
        "formats": list(c.formats),
        "gate_score_total": c.gate_score_total,
        "gate_scores": c.gate_scores,
        "gate_verdict": c.gate_verdict,
        "status": c.status,
        "created_at": c.created_at,
        "updated_at": c.updated_at,
        "cover_path": c.cover_path,
        "inline_images": list(c.inline_images),
    }


def pub_dict(p: Publication) -> dict[str, Any]:
    """Publication → dict（字段 1:1）。"""
    return {
        "id": p.id,
        "content_id": p.content_id,
        "platform": p.platform,
        "account_id": p.account_id,
        "scheduled_at": p.scheduled_at,
        "published_at": p.published_at,
        "platform_post_id": p.platform_post_id,
        "platform_url": p.platform_url,
        "error": p.error,
        "retry_count": p.retry_count,
        "status": p.status,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
    }


def metric_dict(
    m: Metric, *, include_raw: bool = False,
) -> dict[str, Any]:
    """Metric → dict。

    include_raw=False（默认）→ 丢 raw 字段（平台原始 JSON，含敏感
    内部字段如 cookie 痕迹）。webui 列表/详情用 False；调试 / 排查
    时传 True。
    """
    out = {
        "publication_id": m.publication_id,
        "collected_at": m.collected_at,
        "views": m.views,
        "likes": m.likes,
        "comments": m.comments,
        "shares": m.shares,
        "followers_delta": m.followers_delta,
    }
    if include_raw:
        out["raw"] = m.raw
    return out


# ── 内容输出目录枚举 ──────────────────────────────────────


# 内容输出目录约定：output/YYYY-MM-DD/<content_id>/
# 派生文件（M2-3）：toutiao.md / xiaohongshu/{slides.json,caption.md,tags.txt} / x/thread.md
# 图卡（M2-4）：xiaohongshu/cover.png + xiaohongshu/card-N.png
# 封面/插图（M-x）：cover.png + images/inline-N.png

_KNOWN_DERIVATIVE_PATHS = [
    ("toutiao.md", "toutiao", "text"),
    ("xiaohongshu/slides.json", "xiaohongshu", "slides"),
    ("xiaohongshu/caption.md", "xiaohongshu", "caption"),
    ("xiaohongshu/tags.txt", "xiaohongshu", "tags"),
    ("x/thread.md", "x", "text"),
    ("cover.png", None, "cover"),
    ("xiaohongshu/cover.png", "xiaohongshu", "cover"),
    ("xiaohongshu/card-1.png", "xiaohongshu", "card"),
    ("xiaohongshu/card-2.png", "xiaohongshu", "card"),
    ("xiaohongshu/card-3.png", "xiaohongshu", "card"),
    ("xiaohongshu/card-4.png", "xiaohongshu", "card"),
    ("xiaohongshu/card-5.png", "xiaohongshu", "card"),
    ("images/inline-1.png", None, "inline_image"),
    ("images/inline-2.png", None, "inline_image"),
    ("images/inline-3.png", None, "inline_image"),
]


def _content_output_dir(content: Content) -> Path | None:
    """从 Content.canonical_path 反推内容目录。

    canonical_path 形如 `output/2026-07-05/<content_id>/canonical.md`
    → 返回 `output/2026-07-05/<content_id>/`。解析失败 → None。
    """
    p = Path(content.canonical_path)
    if p.name != "canonical.md":
        return None
    return p.parent


def list_content_files(content: Content) -> list[dict[str, Any]]:
    """枚举一条 content 的派生文件 + 图卡 + 插图。

    返回 list[dict]，每项：
        {path, platform, kind, exists, size}
    已知路径全部枚举（即使文件不存在——前端 UI 可据此判断「缺图卡」
    等状态，exists=False）。未知路径不返回。
    """
    base = _content_output_dir(content)
    if base is None or not base.exists():
        # 目录不存在：返回所有已知项，exists=False
        return [
            {
                "path": rel, "platform": plat, "kind": kind,
                "exists": False, "size": 0,
            }
            for rel, plat, kind in _KNOWN_DERIVATIVE_PATHS
        ]
    out = []
    for rel, plat, kind in _KNOWN_DERIVATIVE_PATHS:
        full = base / rel
        exists = full.is_file()
        size = full.stat().st_size if exists else 0
        out.append({
            "path": rel, "platform": plat, "kind": kind,
            "exists": exists, "size": size,
        })
    return out


def content_output_url_prefix(content: Content) -> str:
    """内容输出目录的 `/output/...` URL 前缀（末尾带 `/`）。

    用于 md_to_html 把 canonical.md 里的图片相对路径（如
    `images/inline-1.png`）解析成可访问的 URL。解析失败（canonical_path
    不是标准 `output/.../canonical.md` 形式）返回空串。
    """
    base = _content_output_dir(content)
    if base is None:
        return ""
    return f"/output/{str(base).removeprefix('output/')}/"


def content_image_urls(content: Content) -> dict[str, Any]:
    """把 cover_path / inline_images 转 `/output/...` 形式的 URL。

    返回 dict：
        {cover: str | None, inline: list[str]}
    文件存在与否不判断——前端拿 URL 后让浏览器自己 404（与 R7-2 修复
    一致：/output 是无脑挂静态目录）。封面/插图路径为相对 output/，
    前端拼 host:port/output/<path>。
    """
    cover_url = None
    if content.cover_path:
        # cover_path 是 output/.../cover.png 相对路径
        cover_url = f"/output/{content.cover_path.removeprefix('output/')}"
    inline_urls = []
    for img in content.inline_images:
        inline_urls.append(f"/output/{img.removeprefix('output/')}")
    return {"cover": cover_url, "inline": inline_urls}


# ── 写接口（P1 不接路由，P2 canonical 在线编辑用）────────


# 越狱防护：解析后的绝对路径必须以该 content 输出目录为前缀。
# 用 realpath 而非 resolve——不要求路径存在（canonical.md 可能未生成）。
def _safe_resolve(path: str, content_dir: Path) -> Path | None:
    """解析 path 为绝对路径并校验不越狱。

    Returns: 解析后绝对路径（可能尚未存在）。越狱或非法 → None。

    越狱防护两层：
      1. 路径含 NUL / 空串 → 拒
      2. os.path.normpath 解析 .. 后，必须以 content_dir 为前缀
    """
    if not path or "\x00" in path:
        return None
    # 先归一化（解析 ../.）
    normalized = os.path.normpath(path)
    # 归一化后若含 .. 仍越出（绝对路径不可能含 ..，相对路径可能）
    if normalized.startswith("..") and os.sep in normalized[:3]:
        return None
    p = Path(normalized)
    if not p.is_absolute():
        p = (content_dir / p)
    abs_path = p.absolute()
    content_dir_abs = content_dir.absolute()
    # 关键：归一化后再比较（处理 ..）
    try:
        abs_path.relative_to(content_dir_abs)
    except ValueError:
        return None
    # 二次校验：normpath(absolute) 也必须以 normpath(content_dir) 开头
    abs_norm = os.path.normpath(str(abs_path))
    base_norm = os.path.normpath(str(content_dir_abs))
    if not abs_norm.startswith(base_norm + os.sep) and abs_norm != base_norm:
        return None
    return abs_path


def write_canonical_jailed(
    content: Content, markdown: str,
) -> int:
    """原子写 canonical.md，越狱防护 + tmp→rename。

    路径：content.canonical_path 解析后必须以 content_dir 为前缀。
    写流程：
        1. 校验 markdown 不是 bytes（仅 str）
        2. 解析目标路径，越狱 → ValueError
        3. 写 <target>.tmp 后 os.replace 覆盖（HARD_PARTS §5 模式）
    Returns: 写入字节数。失败抛 OSError 或 ValueError，调用方处理。

    注意：M10 P1 不暴露此函数给 HTTP 路由——为 P2 canonical 在线
    编辑铺路，本期先写测试覆盖越狱防护。
    """
    if not isinstance(markdown, str):
        raise TypeError(
            f"markdown must be str, got {type(markdown).__name__}"
        )
    target = Path(content.canonical_path)
    if not target.is_absolute():
        # 相对路径视为相对 cwd——但仍要校验不越狱
        target_abs = target.absolute()
    else:
        target_abs = target
    content_dir = target_abs.parent
    safe_target = _safe_resolve(str(target_abs), content_dir)
    if safe_target is None:
        raise ValueError(
            f"canonical_path escapes content_dir: {content.canonical_path!r}"
        )
    safe_target.parent.mkdir(parents=True, exist_ok=True)
    # 原子写：tmp→rename（HARD_PARTS §5）
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=safe_target.parent,
        prefix=f".{safe_target.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            n = f.write(markdown)
        os.replace(tmp_path, safe_target)
        return n
    except Exception:
        # 清理 tmp（rename 失败时 tmp 可能残留）
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


__all__ = [
    "topic_dict",
    "content_dict",
    "pub_dict",
    "metric_dict",
    "list_content_files",
    "content_image_urls",
    "content_output_url_prefix",
    "write_canonical_jailed",
]
