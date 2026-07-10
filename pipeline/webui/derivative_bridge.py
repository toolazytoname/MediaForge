"""阶段 B：单条 content 衍生（小红书 slides）+ 真实 AI 出图（cover + inline）。

薄封装层（M10 P2），不重写业务逻辑，只做：
  - 内容存在性 + 状态校验
  - 调用 creators.derivative.derive_one（限定 xiaohongshu）
  - 调用 creators.image_gen.generate_image（出 cover + N inline）
  - 出图后回写 Content.cover_path / inline_images（db helper 封装）
  - 错误分类 → 上层 API 路由映射到 HTTP 码

设计要点（与 M10 P2 阶段 A creation_bridge.py 同构）：
  - 不重写 create_one / derive_one / generate_image 业务逻辑
  - now 显式注入（单测可控），缺省 = db.now_utc()
  - 状态白名单：draft / gated / approved / rejected_by_human
    （显式排除 done/published 已发出去别再改 + failed/discarded 终态）
  - BudgetExceeded 原样上抛（系统级 → 503）
  - image provider 不可用（key 缺 / 4xx / 重试耗尽）→ 503 显式失败
    **不静默降级到模板渲染**（用户决策：保持图片真实性的护栏）

注：M10 P2 阶段 A 创建的 content 必为 draft；本 bridge 也允许 gated /
approved / rejected_by_human，原因是用户可能在审过 / 审打回后还想补出
衍生稿（无需重走 create 阶段）。done / published 状态禁止（已发出去
别再改，避免误导发布链路）。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from pipeline import db
from pipeline.creators import derivative
from pipeline.creators import image_gen
# 不做 `from image_gen import generate_image` —— 那样会在 import 时绑定函数名，
# 测试无法 monkeypatch.setattr(image_gen, "generate_image", ...) 拦截。
# 改走模块属性查找，调用时按 image_gen.generate_image 解析，可被 monkeypatch。
from pipeline.models import Content, ContentStatus
from pipeline.utils.errors import BudgetExceeded, CreateError


# ── Exceptions（API 层映射：404 / 400 / 503 / 503 / 500）────────


class ContentNotFoundError(ValueError):
    """content_id 不存在 → 404。前端按 error code 区分。"""


class ContentStatusError(ValueError):
    """content status 不在允许集合 → 400。前端按 error code 区分。"""


# 注：ImageProviderError 定义在 pipeline.creators.image_gen（M-x 与 bridge
# 共用），本文件 re-export 方便调用方统一 import：
# `from pipeline.webui.derivative_bridge import ImageProviderError`
ImageProviderError = image_gen.ImageProviderError


# ── 状态白名单 ─────────────────────────────────────────────


# 允许触发单条衍生/出图的状态集合
#   - draft        (M10 P2 阶段 A 刚创建，gate 都没过)
#   - gated        (过门禁待审)
#   - approved     (人审通过)
#   - rejected_by_human (人审打回，运营想重出图也是合理的)
# 显式排除：
#   - done / published (已发出去别再改)
#   - failed / discarded (终态，不再折腾)
_ALLOWED_STATUSES: frozenset[str] = frozenset({
    ContentStatus.DRAFT.value,
    ContentStatus.GATED.value,
    ContentStatus.APPROVED.value,
    ContentStatus.REJECTED_BY_HUMAN.value,
})


def _check_status(c: Content) -> None:
    if c.status not in _ALLOWED_STATUSES:
        raise ContentStatusError(
            f"content {c.id} status={c.status!r} not in allowed "
            f"{sorted(_ALLOWED_STATUSES)}"
        )


# ── 1) 衍生小红书 ─────────────────────────────────────────


def derive_xhs_for_content(
    conn: sqlite3.Connection,
    content_id: str,
    *,
    now: str | None = None,
) -> dict[str, Any]:
    """为单条 content 调 derivative.derive_one 但限定 xiaohongshu 平台。

    复用 pipeline.creators.derivative.derive_one（已有 tmp→rename 幂等 +
    围栏剥离 + 错误隔离），不重写。

    Args:
        conn: SQLite 连接（由调用方管理生命周期）。
        content_id: 内容 id（'c_' 前缀）。
        now: ISO8601 UTC 字符串。缺省 = db.now_utc()。测试可注入固定值。

    Returns:
        dict:
            - slides_count (int): slide 张数（5-7）
            - caption_chars (int): caption 字符数
            - tags (list[str]): 标签列表

    Raises:
        ContentNotFoundError: content_id 不存在。
        ContentStatusError: content 状态不在允许集合。
        CreateError: xhs 派生失败（LLM/解析/写盘）→ 500。
        BudgetExceeded: LLM 预算超限（系统级，原样上抛）→ 503。
    """
    if now is None:
        now = db.now_utc()

    # 1. 读 + 状态校验
    c = db.get_content(conn, content_id)
    if c is None:
        raise ContentNotFoundError(f"content {content_id} not found")
    _check_status(c)

    # 2. 调 derive_one 限定 xiaohongshu（BudgetExceeded / CreateError 原样上抛）
    output_dir = Path(c.canonical_path).parent
    result = derivative.derive_one(
        c,
        output_dir=output_dir,
        now=now,
        conn=conn,
        platforms=("xiaohongshu",),
    )

    # 3. xhs 失败 → 抛 CreateError（让 API 层返 500）
    # 注意：derive_one 内部对单平台失败用 try/except CreateError 隔离，
    # 不抛；这里需要把「单条 + 单平台」失败显式抛给上层。
    if result.xiaohongshu is None:
        raise CreateError(
            f"xhs derivative failed for content {content_id}"
        )

    # 4. 派生成功 → 写回 contents.formats（合并语义，参考 derivative._update_formats_field）
    derivative._update_formats_field(
        conn, content_id, ("xiaohongshu",), now
    )

    return {
        "slides_count": len(result.xiaohongshu.slides),
        "caption_chars": len(result.xiaohongshu.caption),
        "tags": list(result.xiaohongshu.tags),
    }


# ── 2) 真实 AI 出图 ──────────────────────────────────────


# 默认 inline 数量（无 xhs/slides.json 时）—— XHS 常见 5-7 张
_DEFAULT_INLINE_COUNT = 5


def _resolve_inline_count(content_dir: Path) -> int:
    """从 xhs/slides.json 读 slide 数；缺省 = 5。"""
    slides_path = content_dir / "xiaohongshu" / "slides.json"
    if not slides_path.is_file():
        return _DEFAULT_INLINE_COUNT
    try:
        slides = json.loads(slides_path.read_text(encoding="utf-8"))
        if isinstance(slides, list) and slides:
            return len(slides)
    except (OSError, json.JSONDecodeError):
        pass
    return _DEFAULT_INLINE_COUNT


def _query_image_cost_usd(
    conn: sqlite3.Connection,
    content_id: str,
) -> float:
    """查 llm_calls 中本 content 累计 create_cover + create_image cost。
    配合调用前的 cost_before 算 diff，拿到本次调用的增量成本——
    避免多次调用叠加历史（旧方案用 since_iso 过滤，受 mock 时间戳影响会
    漏算；改用 diff 更鲁棒且语义清晰「本次出图花了多少」）。
    """
    row = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) AS c FROM llm_calls "
        "WHERE ref_id=? AND stage IN ('create_cover', 'create_image')",
        (content_id,),
    ).fetchone()
    return float(row["c"])


def _build_cover_prompt(title: str, aspect_ratio: str) -> str:
    """封面 prompt 模板（6 元素：主体+场景+风格+光照+构图+质量）。"""
    return (
        f"{title} — 作为主体。\n"
        f"场景：抽象科技感背景，干净的渐变与几何元素，无具体产品。\n"
        f"风格：现代扁平化插画 + 微妙层次，专业编辑级。\n"
        f"光照：柔和漫射光，顶部轻度高光，无硬阴影。\n"
        f"构图：超宽 {aspect_ratio}，留白充足。\n"
        f"质量：高视觉密度，丰富细节，构图精致。"
    )


def _build_inline_prompt(
    slide_index: int, slide_title: str, slide_body: str,
) -> str:
    """文中插图 prompt 模板（方形 1:1，用 slide 的 title/body 作主体）。"""
    title_part = (slide_title or f"slide {slide_index}").strip()
    body_part = (slide_body or "").strip()
    return (
        f"小红书第 {slide_index} 张卡片：{title_part}\n"
        f"主体：{body_part or '通用技术示意图'}\n"
        f"场景：干净的背景，简洁的视觉元素，无具体产品。\n"
        f"风格：扁平化信息图 + 微妙阴影，专业技术文档级。\n"
        f"光照：均匀漫射光，主体清晰可辨。\n"
        f"构图：方形 1:1 居中，留白充足。\n"
        f"质量：信息密度高，构图简洁，视觉重点突出。"
    )


def _load_slide_for_prompt(
    content_dir: Path, slide_index: int,
) -> tuple[str, str] | None:
    """从 xhs/slides.json 读第 slide_index 张 (1-based) 的 (title, body)；
    读不到返回 None。
    """
    slides_path = content_dir / "xiaohongshu" / "slides.json"
    if not slides_path.is_file():
        return None
    try:
        slides = json.loads(slides_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(slides, list) or slide_index < 1 or slide_index > len(slides):
        return None
    s = slides[slide_index - 1]
    if not isinstance(s, dict):
        return None
    return (str(s.get("title", "")), str(s.get("body", "")))


def generate_images_for_content(
    conn: sqlite3.Connection,
    content_id: str,
    *,
    aspect_ratio: str = "3:4",
    now: str | None = None,
) -> dict[str, Any]:
    """为单条 content 调 image_gen.generate_image 出 cover + N inline。

    复用 pipeline.creators.image_gen.generate_image（已有重试+预算+审计+原子
    写），不重写。

    Args:
        conn: SQLite 连接（由调用方管理生命周期）。
        content_id: 内容 id（'c_' 前缀）。
        aspect_ratio: 封面图比例（默认 3:4，XHS 竖图标准）。inline 固定 1:1。
        now: ISO8601 UTC 字符串。缺省 = db.now_utc()。测试可注入固定值。

    Returns:
        dict:
            - cover_path (str): 封面相对路径（与 canonical_path 同形：
              `output/<date>/<content_id>/cover.png`）
            - inline_images (list[str]): 文中插图相对路径列表
            - cost_usd (float): 本次出图累计成本（从 llm_calls 查 create_cover+
              create_image 求和；generate_image 不返回 cost_usd）

    Raises:
        ContentNotFoundError: content_id 不存在。
        ContentStatusError: content 状态不在允许集合。
        ImageProviderError: provider 不可用（key 缺 / 4xx / 重试耗尽 / 未初始化）。
        BudgetExceeded: LLM 预算超限（系统级，原样上抛）→ 503。
        OSError: 文件系统写盘失败（image_gen._write_atomic 抛）→ 500。
    """
    if now is None:
        now = db.now_utc()

    # 1. 读 + 状态校验
    c = db.get_content(conn, content_id)
    if c is None:
        raise ContentNotFoundError(f"content {content_id} not found")
    _check_status(c)

    # 2. 准备路径（与 canonical_path 同形：output/<date>/<id>/...）
    content_dir = Path(c.canonical_path).parent
    images_dir = content_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    # relative-to-output 形式（与 Content.canonical_path 一致，serialize 层
    # 把它拼到 /output/ URL）
    content_dir_rel = str(content_dir)

    # 3. 解析 inline 数量（slides.json 优先，否则默认 5）
    n_inline = _resolve_inline_count(content_dir)

    # 4. 累计成本基线（diff 算本次出图成本；多次调用不叠加）
    cost_before = _query_image_cost_usd(conn, content_id)

    # 5. 调 image_gen 出图（先 cover 再 inline；BudgetExceeded 原样上抛）
    try:
        # ── 封面 ──
        cover_out = content_dir / "cover.png"
        cover_prompt = _build_cover_prompt(c.title, aspect_ratio)
        image_gen.generate_image(
            cover_prompt,
            out_path=cover_out,
            aspect_ratio=aspect_ratio,
            n=1,
            stage="create_cover",
            ref_id=content_id,
            conn=conn,
        )

        # ── 文中插图 ──
        inline_paths: list[str] = []
        for i in range(1, n_inline + 1):
            inline_out = images_dir / f"inline-{i}.png"
            slide_info = _load_slide_for_prompt(content_dir, i)
            if slide_info is not None:
                inline_prompt = _build_inline_prompt(i, *slide_info)
            else:
                inline_prompt = _build_inline_prompt(i, f"slide {i}", "")
            image_gen.generate_image(
                inline_prompt,
                out_path=inline_out,
                aspect_ratio="1:1",
                n=1,
                stage="create_image",
                ref_id=content_id,
                conn=conn,
            )
            inline_paths.append(f"{content_dir_rel}/images/inline-{i}.png")
    except image_gen.RetryableError as e:
        # 重试 3 次仍失败 → provider 不可用
        raise ImageProviderError(
            f"image gen retry exhausted: {e}"
        ) from e
    except ValueError as e:
        # provider 不可用（key 缺 / 4xx / 响应残缺 / 参数错）——
        # image_gen 内部用 ValueError 报契约错误
        raise ImageProviderError(
            f"image provider error: {e}"
        ) from e
    except RuntimeError as e:
        # provider 未初始化（_require_provider 抛 RuntimeError）
        raise ImageProviderError(
            f"image provider not initialized: {e}"
        ) from e
    # BudgetExceeded 自然上抛（503）

    # 6. 写回 Content.cover_path / inline_images（db helper 封装，UI 层不裸 SQL）
    cover_relpath = f"{content_dir_rel}/cover.png"
    db.set_content_cover(conn, content_id, cover_relpath)
    db.set_content_inline_images(conn, content_id, inline_paths)

    # 7. 累计成本（diff = 本次出图成本，绕开时间戳过滤的脆弱性）
    cost_after = _query_image_cost_usd(conn, content_id)
    cost_usd = max(0.0, cost_after - cost_before)

    return {
        "cover_path": cover_relpath,
        "inline_images": inline_paths,
        "cost_usd": cost_usd,
    }


__all__ = [
    "derive_xhs_for_content",
    "generate_images_for_content",
    "ContentNotFoundError",
    "ContentStatusError",
    "ImageProviderError",
    "BudgetExceeded",  # re-export 方便调用方
    "CreateError",  # re-export 方便调用方
]
