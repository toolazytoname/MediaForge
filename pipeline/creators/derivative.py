"""派生格式生成（M2-3 一料多吃）。

输入：canonical.md（gate 通过的内容）
输出：每个平台的原生格式，写到 output/<date>/<content_id>/{platform}/

平台约定（PRD §3.2 + ARCHITECTURE §8）：
  - toutiao 头条     → toutiao.md（标题 3 选 1 + 短正文 + 配图占位）
  - xiaohongshu 小红书 → xiaohongshu/slides.json + caption.md + tags.txt
  - x  X/Twitter     → x/thread.md（5-10 条英文推文，每条 ≤ 260 字符）

每个派生函数独立调 LLM，走 complete_json（自动重试一次 JSON 失败）。
幂等：覆盖写（tmp→rename 模式）。
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from pipeline.creators import llm as llm_mod
from pipeline.creators.llm import complete_json
from pipeline.models import Content
from pipeline.utils.errors import BudgetExceeded, CreateError
from pipeline.utils.log import get_logger, log_event

_LOGGER = get_logger("pipeline.creators.derivative", "logs")


# ── prompts ────────────────────────────────────────────────

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_TOUTIAO_PROMPT = _PROMPTS_DIR / "toutiao.md"
_XHS_PROMPT = _PROMPTS_DIR / "xiaohongshu.md"
_X_PROMPT = _PROMPTS_DIR / "x.md"


def _render_toutiao(*, title: str, canonical_md: str) -> str:
    return _TOUTIAO_PROMPT.read_text(encoding="utf-8").format(
        title=title, canonical_md=canonical_md
    )


def _render_xhs(*, title: str, canonical_md: str) -> str:
    return _XHS_PROMPT.read_text(encoding="utf-8").format(
        title=title, canonical_md=canonical_md
    )


def _render_x(*, title: str, canonical_md: str) -> str:
    return _X_PROMPT.read_text(encoding="utf-8").format(
        title=title, canonical_md=canonical_md
    )


# ── 输出数据类 ─────────────────────────────────────────────

@dataclass(frozen=True)
class ToutiaoOutput:
    titles: tuple[str, ...]   # 3 候选
    body: str                 # 含 H1 + 短正文


@dataclass(frozen=True)
class XiaohongshuSlide:
    type: str    # 'cover' | 'content' | 'action'
    title: str
    body: str


@dataclass(frozen=True)
class XiaohongshuOutput:
    slides: tuple[XiaohongshuSlide, ...]
    caption: str
    tags: tuple[str, ...]


@dataclass(frozen=True)
class XOutput:
    tweets: tuple[str, ...]


@dataclass(frozen=True)
class DerivativeResult:
    """单条 content 的派生结果（全部三个平台）。"""
    content_id: str
    toutiao: ToutiaoOutput | None = None
    xiaohongshu: XiaohongshuOutput | None = None
    x: XOutput | None = None
    failed_platforms: tuple[str, ...] = field(default_factory=tuple)


# ── 解析与校验 ─────────────────────────────────────────────

_VALID_TOUTIAO_TITLE_LEN = 36     # 头条大图标题上限约 40 字；严苛 28 模型常溢出
_X_TWEET_MAX_LEN = 280              # X 标准单推字符上限
_XHS_BODY_MAX_LEN = 100             # 小红书图卡单张文字上限（屏幕显示舒适）
_XHS_TAG_COUNT_MIN, _XHS_TAG_COUNT_MAX = 3, 10
_XHS_CAPTION_MIN, _XHS_CAPTION_MAX = 50, 500
_XHS_SLIDES_MIN, _XHS_SLIDES_MAX = 5, 7


def _parse_toutiao(text: str) -> ToutiaoOutput:
    cleaned = _strip_fence(text)
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise CreateError(f"toutiao JSON parse failed: {e}") from e
    if not isinstance(obj, dict):
        raise CreateError(f"toutiao response not dict: {text[:200]!r}")
    titles = obj.get("titles")
    if not isinstance(titles, list) or len(titles) != 3:
        raise CreateError(f"toutiao titles must be list of 3: {titles!r}")
    if not all(isinstance(t, str) for t in titles):
        raise CreateError(f"toutiao titles not all strings: {titles!r}")
    for i, t in enumerate(titles):
        if len(t) > _VALID_TOUTIAO_TITLE_LEN:
            raise CreateError(
                f"toutiao title[{i}] too long ({len(t)} > {_VALID_TOUTIAO_TITLE_LEN}): "
                f"{t!r}"
            )
    body = obj.get("body")
    if not isinstance(body, str) or not body.strip():
        raise CreateError(f"toutiao body missing/empty: {body!r}")
    return ToutiaoOutput(titles=tuple(titles), body=body)


def _parse_xhs(text: str) -> XiaohongshuOutput:
    cleaned = _strip_fence(text)
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise CreateError(f"xiaohongshu JSON parse failed: {e}") from e
    if not isinstance(obj, dict):
        raise CreateError(f"xiaohongshu response not dict: {text[:200]!r}")
    slides_raw = obj.get("slides")
    if not isinstance(slides_raw, list):
        raise CreateError(f"xiaohongshu slides not a list: {slides_raw!r}")
    if not (_XHS_SLIDES_MIN <= len(slides_raw) <= _XHS_SLIDES_MAX):
        raise CreateError(
            f"xiaohongshu slides count out of range "
            f"({_XHS_SLIDES_MIN}-{_XHS_SLIDES_MAX}): {len(slides_raw)}"
        )
    slides: list[XiaohongshuSlide] = []
    valid_types = {"cover", "content", "action"}
    for s in slides_raw:
        if not isinstance(s, dict):
            raise CreateError(f"slide not dict: {s!r}")
        t = s.get("type", "")
        if t not in valid_types:
            raise CreateError(f"slide type invalid: {t!r}")
        title = str(s.get("title", ""))
        body = str(s.get("body", ""))
        if len(body) > _XHS_BODY_MAX_LEN:
            raise CreateError(
                f"slide body too long ({len(body)} > {_XHS_BODY_MAX_LEN}): "
                f"{body!r}"
            )
        slides.append(XiaohongshuSlide(type=t, title=title, body=body))
    if slides[0].type != "cover":
        raise CreateError(f"first slide must be 'cover': {slides[0].type}")
    if slides[-1].type != "action":
        raise CreateError(f"last slide must be 'action': {slides[-1].type}")
    caption = obj.get("caption", "")
    if not isinstance(caption, str):
        raise CreateError(f"xiaohongshu caption not str: {caption!r}")
    if not (_XHS_CAPTION_MIN <= len(caption) <= _XHS_CAPTION_MAX):
        raise CreateError(
            f"xiaohongshu caption length {len(caption)} "
            f"out of [{_XHS_CAPTION_MIN}, {_XHS_CAPTION_MAX}]"
        )
    tags = obj.get("tags")
    if not isinstance(tags, list):
        raise CreateError(f"xiaohongshu tags not list: {tags!r}")
    if not (_XHS_TAG_COUNT_MIN <= len(tags) <= _XHS_TAG_COUNT_MAX):
        raise CreateError(
            f"xiaohongshu tags count {len(tags)} "
            f"out of [{_XHS_TAG_COUNT_MIN}, {_XHS_TAG_COUNT_MAX}]"
        )
    if not all(isinstance(t, str) for t in tags):
        raise CreateError(f"xiaohongshu tags not all strings: {tags!r}")
    return XiaohongshuOutput(
        slides=tuple(slides),
        caption=caption,
        tags=tuple(tags),
    )


def _parse_x(text: str) -> XOutput:
    cleaned = _strip_fence(text)
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise CreateError(f"x thread JSON parse failed: {e}") from e
    if not isinstance(obj, dict):
        raise CreateError(f"x thread response not dict: {text[:200]!r}")
    tweets = obj.get("tweets")
    if not isinstance(tweets, list):
        raise CreateError(f"x tweets not list: {tweets!r}")
    if not (5 <= len(tweets) <= 10):
        raise CreateError(
            f"x tweets count {len(tweets)} out of [5, 10]"
        )
    for i, t in enumerate(tweets):
        if not isinstance(t, str):
            raise CreateError(f"x tweet[{i}] not str: {t!r}")
        if len(t) > _X_TWEET_MAX_LEN:
            raise CreateError(
                f"x tweet[{i}] too long ({len(t)} > {_X_TWEET_MAX_LEN}): "
                f"{t!r}"
            )
    return XOutput(tweets=tuple(tweets))


def _strip_fence(text: str) -> str:
    """剥 ```json ... ``` 或 ``` ... ``` 围栏（防御性 LLM 行为）。

    处理三种常见格式：
      - ```` ```json\\n{...}\\n``` ````
      - ```` ```\\n{...}\\n``` ````
      - ```` ```json{...}\\n``` ````（语言标签后无换行——LLM 偶尔产出）

    用正则剥首行围栏（含语言标签 + 可选换行），再剥尾部围栏。
    """
    import re

    s = text.strip()
    m = re.match(r"^```[a-zA-Z]*\n?", s)
    if not m:
        return s
    body = s[m.end():]
    if body.rstrip().endswith("```"):
        body = body.rstrip()[:-3].rstrip()
    return body


# ── 三个派生函数 ───────────────────────────────────────────

def derive_toutiao(
    *,
    title: str,
    canonical_md: str,
    conn: sqlite3.Connection | None = None,
    ref_id: str | None = None,
) -> ToutiaoOutput:
    """生成头条版本。"""
    prompt = _render_toutiao(title=title, canonical_md=canonical_md)
    try:
        result = complete_json(
            prompt,
            stage="derive_toutiao",
            ref_id=ref_id,
            model_tier="creative",
            max_tokens=8192,
            conn=conn,
            parse=_parse_toutiao,
            max_retries=1,
        )
    except llm_mod.RetryableError as e:
        raise CreateError(
            f"toutiao LLM retry exhausted for ref={ref_id}: {e}"
        ) from e
    return result


def derive_xiaohongshu(
    *,
    title: str,
    canonical_md: str,
    conn: sqlite3.Connection | None = None,
    ref_id: str | None = None,
) -> XiaohongshuOutput:
    """生成小红书图卡结构。"""
    prompt = _render_xhs(title=title, canonical_md=canonical_md)
    try:
        result = complete_json(
            prompt,
            stage="derive_xiaohongshu",
            ref_id=ref_id,
            model_tier="creative",
            max_tokens=6144,
            conn=conn,
            parse=_parse_xhs,
            max_retries=1,
        )
    except llm_mod.RetryableError as e:
        raise CreateError(
            f"xiaohongshu LLM retry exhausted for ref={ref_id}: {e}"
        ) from e
    return result


def derive_x(
    *,
    title: str,
    canonical_md: str,
    conn: sqlite3.Connection | None = None,
    ref_id: str | None = None,
) -> XOutput:
    """生成 X thread。"""
    prompt = _render_x(title=title, canonical_md=canonical_md)
    try:
        result = complete_json(
            prompt,
            stage="derive_x",
            ref_id=ref_id,
            model_tier="creative",
            max_tokens=4096,
            conn=conn,
            parse=_parse_x,
            max_retries=1,
        )
    except llm_mod.RetryableError as e:
        raise CreateError(
            f"x LLM retry exhausted for ref={ref_id}: {e}"
        ) from e
    return result


# ── 文件写入（tmp→rename）────────────────────────────────

def _write_atomic(path: Path, content: str) -> None:
    """原子写入：tmp → rename（HARD_PARTS §5 幂等）。"""
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    tmp = parent / (path.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    tmp.write_text(content, encoding="utf-8")
    tmp.rename(path)


def _write_toutiao(out_dir: Path, output: ToutiaoOutput) -> None:
    """写 toutiao.md（含 3 候选标题 + 正文）。"""
    body = (
        "# 候选标题（选 1）\n\n"
        + "\n".join(f"{i+1}. {t}" for i, t in enumerate(output.titles))
        + "\n\n---\n\n"
        + output.body
    )
    _write_atomic(out_dir / "toutiao.md", body)


def _write_xhs(out_dir: Path, output: XiaohongshuOutput) -> None:
    """写 xiaohongshu/{slides.json, caption.md, tags.txt}。"""
    xhs_dir = out_dir / "xiaohongshu"
    slides_payload = [
        {"type": s.type, "title": s.title, "body": s.body}
        for s in output.slides
    ]
    _write_atomic(
        xhs_dir / "slides.json",
        json.dumps(slides_payload, ensure_ascii=False, indent=2),
    )
    _write_atomic(xhs_dir / "caption.md", output.caption)
    _write_atomic(xhs_dir / "tags.txt", "\n".join(output.tags))


def _write_x(out_dir: Path, output: XOutput) -> None:
    """写 x/thread.md（每条推文为编号列表）。"""
    x_dir = out_dir / "x"
    body_lines = [
        f"{i+1}/{len(output.tweets)} {tweet}"
        for i, tweet in enumerate(output.tweets)
    ]
    _write_atomic(x_dir / "thread.md", "\n\n".join(body_lines))


# ── 单条 content 派生编排 ─────────────────────────────────

def derive_one(
    content: Content,
    *,
    output_dir: Path,
    now: str,
    conn: sqlite3.Connection | None = None,
    platforms: tuple[str, ...] = ("toutiao", "xiaohongshu", "x"),
) -> DerivativeResult:
    """为单条 gated content 派生各平台格式。

    Args:
        content: Content 记录（status=gated）
        output_dir: content_dir 根目录（= output/<date>/<content_id>/）
        now: ISO8601 UTC
        conn: DB 连接
        platforms: 要派生的平台子集（默认全部 3 个）

    Returns:
        DerivativeResult
          - 成功的平台填入对应字段
          - 失败的平台列入 failed_platforms

    Raises:
        CreateError: 整条 content IO 失败（canonical.md 缺失/损坏）——不抛，
                      而是把所有平台标 failed，DerivativeResult.failed_platforms
                      含全部请求平台。
        BudgetExceeded: 系统性错误（不隔离，原样上抛编排层终止整批）
    """
    canonical_path = Path(content.canonical_path)
    try:
        canonical_md = canonical_path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, UnicodeDecodeError) as e:
        # 整条 content 不可用 → 把所有请求平台标 failed（IO 隔离）
        return DerivativeResult(
            content_id=content.id,
            failed_platforms=tuple(platforms),
        )

    title = content.title
    failed: list[str] = []

    toutiao = None
    xiaohongshu = None
    x = None

    # 注意：只捕 CreateError；BudgetExceeded 让其自然上抛（审计 Bug 2）
    if "toutiao" in platforms:
        try:
            toutiao = derive_toutiao(
                title=title, canonical_md=canonical_md,
                conn=conn, ref_id=content.id,
            )
            _write_toutiao(output_dir, toutiao)
        except CreateError as e:
            log_event(_LOGGER, logging.WARNING, f"toutiao derivative failed: {e}",
                       stage="derive", ref_id=content.id)
            failed.append("toutiao")

    if "xiaohongshu" in platforms:
        try:
            xiaohongshu = derive_xiaohongshu(
                title=title, canonical_md=canonical_md,
                conn=conn, ref_id=content.id,
            )
            _write_xhs(output_dir, xiaohongshu)
        except CreateError as e:
            log_event(_LOGGER, logging.WARNING, f"xiaohongshu derivative failed: {e}",
                       stage="derive", ref_id=content.id)
            failed.append("xiaohongshu")

    if "x" in platforms:
        try:
            x = derive_x(
                title=title, canonical_md=canonical_md,
                conn=conn, ref_id=content.id,
            )
            _write_x(output_dir, x)
        except CreateError as e:
            log_event(_LOGGER, logging.WARNING, f"x derivative failed: {e}",
                       stage="derive", ref_id=content.id)
            failed.append("x")

    return DerivativeResult(
        content_id=content.id,
        toutiao=toutiao,
        xiaohongshu=xiaohongshu,
        x=x,
        failed_platforms=tuple(failed),
    )


def derive_for_content(
    content: Content,
    *,
    output_root: Path,
    now: str,
    conn: sqlite3.Connection | None = None,
) -> DerivativeResult:
    """便捷包装：从 content.canonical_path 推出 output_dir。"""
    output_dir = Path(content.canonical_path).parent
    return derive_one(
        content,
        output_dir=output_dir,
        now=now,
        conn=conn,
    )


# ── 批量编排（取 gated → 派生 → 更新 formats）──────────────

def _update_formats_field(
    conn: sqlite3.Connection,
    content_id: str,
    new_formats: tuple[str, ...],
    now: str,
) -> None:
    """合并更新 contents.formats（JSON 数组）——累积而非覆盖（审计 Bug 3）。

    重跑场景：第一轮派生成功 [toutiao] → 第二轮 xhs 成功 / toutiao 失败
    → 最终 formats 应含 [toutiao, xiaohongshu]，不丢失第一轮成果。
    """
    row = conn.execute(
        "SELECT formats FROM contents WHERE id=?", (content_id,)
    ).fetchone()
    existing: set[str] = set()
    if row and row["formats"]:
        try:
            existing = set(json.loads(row["formats"]))
        except (json.JSONDecodeError, TypeError):
            existing = set()
    merged = sorted(existing | set(new_formats))
    conn.execute(
        "UPDATE contents SET formats=?, updated_at=? WHERE id=?",
        (json.dumps(merged), now, content_id),
    )
    conn.commit()


def run_derivative(
    conn: sqlite3.Connection,
    *,
    output_root: Path,
    now: str | None = None,
    platforms: tuple[str, ...] = ("toutiao", "xiaohongshu", "x"),
) -> tuple[DerivativeResult, ...]:
    """为所有 gated content 派生平台格式。

    Args:
        conn: DB 连接
        output_root: output/ 根目录（默认 ./output）
        now: ISO8601 UTC（None 用当前时间）
        platforms: 要派生的平台子集

    Returns:
        每个 gated content 一个 DerivativeResult

    Raises:
        BudgetExceeded: 系统性（终止整批）
    """
    from pipeline import db as db_mod
    from pipeline.models import ContentStatus

    now = now or db_mod.now_utc()
    gated = db_mod.get_contents_by_status(conn, ContentStatus.GATED.value)

    results: list[DerivativeResult] = []
    for content in gated:
        # 单条失败不阻断整体，但 BudgetExceeded 上抛
        result = derive_for_content(
            content,
            output_root=output_root,
            now=now,
            conn=conn,
        )
        # 把派生成功的平台写回 contents.formats
        succeeded: list[str] = []
        if result.toutiao is not None:
            succeeded.append("toutiao")
        if result.xiaohongshu is not None:
            succeeded.append("xiaohongshu")
        if result.x is not None:
            succeeded.append("x")
        if succeeded:
            _update_formats_field(conn, content.id, tuple(succeeded), now)
        results.append(result)
    return tuple(results)