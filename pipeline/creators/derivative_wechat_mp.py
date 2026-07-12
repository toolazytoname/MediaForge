"""公众号图文派生（M13）。

canonical.md → wechat_mp/article.md（正文 markdown）+ meta.json（title/digest）。

不含正文插图占位符解析（v1 无承接机制，禁止 LLM 输出 `[IMAGE:`，见
ARCHITECTURE §8）；封面复用 content.cover_path，不在本模块生成或转存。
正文最终转微信内联样式 HTML 由 publisher 在 publish() 时现转
（pipeline.creators.wechat_html.markdown_to_wechat_html），保持派生产物
markdown-first，与 toutiao/xiaohongshu/x 三个平台一致。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import re

from pipeline.creators import llm as llm_mod
from pipeline.creators.llm import complete_json
from pipeline.utils.errors import CreateError

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_WECHAT_MP_PROMPT = _PROMPTS_DIR / "wechat_mp.md"

_TITLE_MAX_LEN = 64      # 微信草稿标题上限
_DIGEST_MAX_LEN = 120    # draft/add digest 字段上限
_IMAGE_PLACEHOLDER_MARKER = "[IMAGE:"   # v1 无承接机制，出现即拒绝


def _strip_fence(text: str) -> str:
    """剥 ```json ... ``` 或 ``` ... ``` 围栏（防御性 LLM 行为）。

    与 derivative.py 的同名函数逻辑一致，此处内联一份以避免
    derivative_wechat_mp.py ↔ derivative.py 的循环 import。
    """
    s = text.strip()
    m = re.match(r"^```[a-zA-Z]*\n?", s)
    if not m:
        return s
    body = s[m.end():]
    if body.rstrip().endswith("```"):
        body = body.rstrip()[:-3].rstrip()
    return body


def _write_atomic(path: Path, content: str) -> None:
    """原子写入：tmp → rename（HARD_PARTS §5 幂等，与 derivative.py 同名函数一致）。"""
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    tmp = parent / (path.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    tmp.write_text(content, encoding="utf-8")
    tmp.rename(path)


def _render_wechat_mp(*, title: str, canonical_md: str) -> str:
    return _WECHAT_MP_PROMPT.read_text(encoding="utf-8").format(
        title=title, canonical_md=canonical_md
    )


@dataclass(frozen=True)
class WechatMpOutput:
    title: str       # ≤64 字
    digest: str       # ≤120 字
    body_md: str       # 正文 markdown（未转 HTML）


def _parse_wechat_mp(text: str) -> WechatMpOutput:
    cleaned = _strip_fence(text)
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise CreateError(f"wechat_mp JSON parse failed: {e}") from e
    if not isinstance(obj, dict):
        raise CreateError(f"wechat_mp response not dict: {text[:200]!r}")

    title = obj.get("title")
    if not isinstance(title, str) or not title.strip():
        raise CreateError(f"wechat_mp title missing/empty: {title!r}")
    if len(title) > _TITLE_MAX_LEN:
        raise CreateError(
            f"wechat_mp title too long ({len(title)} > {_TITLE_MAX_LEN}): {title!r}"
        )

    digest = obj.get("digest")
    if not isinstance(digest, str) or not digest.strip():
        raise CreateError(f"wechat_mp digest missing/empty: {digest!r}")
    if len(digest) > _DIGEST_MAX_LEN:
        raise CreateError(
            f"wechat_mp digest too long ({len(digest)} > {_DIGEST_MAX_LEN}): {digest!r}"
        )

    body_md = obj.get("body_md")
    if not isinstance(body_md, str) or not body_md.strip():
        raise CreateError(f"wechat_mp body_md missing/empty: {body_md!r}")
    if _IMAGE_PLACEHOLDER_MARKER in body_md:
        raise CreateError(
            f"wechat_mp body_md contains unsupported image placeholder "
            f"({_IMAGE_PLACEHOLDER_MARKER!r}): v1 has no ingestion path for it"
        )

    return WechatMpOutput(title=title, digest=digest, body_md=body_md)


def derive_wechat_mp(
    *,
    title: str,
    canonical_md: str,
    conn: sqlite3.Connection | None = None,
    ref_id: str | None = None,
) -> WechatMpOutput:
    """生成公众号图文版本。"""
    prompt = _render_wechat_mp(title=title, canonical_md=canonical_md)
    try:
        result = complete_json(
            prompt,
            stage="derive_wechat_mp",
            ref_id=ref_id,
            model_tier="creative",
            max_tokens=8192,
            conn=conn,
            parse=_parse_wechat_mp,
            max_retries=1,
        )
    except llm_mod.RetryableError as e:
        raise CreateError(
            f"wechat_mp LLM retry exhausted for ref={ref_id}: {e}"
        ) from e
    return result


def write_wechat_mp(out_dir: Path, output: WechatMpOutput) -> None:
    """写 wechat_mp/article.md（正文）+ wechat_mp/meta.json（title/digest）。"""
    wechat_dir = out_dir / "wechat_mp"
    _write_atomic(wechat_dir / "article.md", output.body_md)
    _write_atomic(
        wechat_dir / "meta.json",
        json.dumps({"title": output.title, "digest": output.digest}, ensure_ascii=False, indent=2),
    )
