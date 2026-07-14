"""公众号图文派生（M13）。

canonical.md → wechat_mp/article.md（正文 markdown）+ meta.json（title/digest）。

LLM 派生阶段禁止输出 `[IMAGE:` 占位符（见 ARCHITECTURE §8）——LLM 派生
wechat_mp 版本时发生在 status=gated，真实插图此时还未生成（要等 status=approved
的 generate-images 阶段），指望 LLM 这一步保留图片不可靠。真正的插图承接改由
`insert_generated_images()` 在 generate-images 阶段之后，用确定性代码把已生成的
`images/inline-N.png` 拼接进 wechat_mp/article.md（见 pipeline.run.cmd_generate_images）。

封面复用 content.cover_path，不在本模块生成或转存。正文最终转微信内联样式 HTML
由 publisher 在 publish() 时现转（pipeline.creators.wechat_html.markdown_to_wechat_html）。
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


# ── 正文真实插图拼接（generate-images 阶段之后调用）───────────

_HEADING_RE = re.compile(r"^##\s+.+$", re.MULTILINE)
_CANONICAL_INLINE_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(images/inline-(\d+)\.png\)")
_SPLICED_MARKER = "](../images/inline-"


def splice_inline_images(body_md: str, images: list[tuple[str, str]]) -> str:
    """把已生成的插图按顺序插入 body_md 的二级标题（`## `）首段之后。

    images：按顺序排列的 (caption, rel_path) 列表。
    图片数 < 标题数：多出的标题不插图。
    图片数 > 标题数：多出的图片依次追加在正文末尾。
    零标题：全部图片依次追加在正文末尾。
    """
    if not images:
        return body_md

    heading_matches = list(_HEADING_RE.finditer(body_md))
    n_slots = min(len(heading_matches), len(images))

    insertions: list[tuple[int, str]] = []
    for heading, (caption, rel_path) in zip(heading_matches[:n_slots], images[:n_slots]):
        # 跳过标题行后的换行，定位到首段正文的起点，再找该段落末尾的空行
        paragraph_start = heading.end()
        while paragraph_start < len(body_md) and body_md[paragraph_start] == "\n":
            paragraph_start += 1
        blank_line_pos = body_md.find("\n\n", paragraph_start)
        pos = blank_line_pos if blank_line_pos != -1 else len(body_md)
        insertions.append((pos, f"\n\n![{caption}]({rel_path})"))

    result = body_md
    for pos, markdown in sorted(insertions, key=lambda item: item[0], reverse=True):
        result = result[:pos] + markdown + result[pos:]

    leftover = images[n_slots:]
    if leftover:
        tail = "\n\n" + "\n\n".join(f"![{caption}]({rel_path})" for caption, rel_path in leftover)
        result = result.rstrip("\n") + tail

    return result


def insert_generated_images(content_dir: Path, canonical_md: str) -> bool:
    """把 canonical_md 里已生成的真实插图拼接进 wechat_mp/article.md。

    从 canonical_md 中按顺序提取 `![caption](images/inline-N.png)` 引用
    （generate-images 阶段生成，替换了原来的 `[IMAGE: caption]` 占位符），
    换算成 wechat_mp/article.md 视角的相对路径 `../images/inline-N.png`，
    调用 splice_inline_images 后原子写回。

    返回 True 表示做了拼接；以下情况返回 False（不算错误）：
      - content_dir/wechat_mp/article.md 不存在（该内容没有派生过 wechat_mp）
      - canonical_md 里没有任何真实插图引用
      - article.md 里已经拼接过（幂等，见 HARD_PARTS §5）
    """
    article_path = content_dir / "wechat_mp" / "article.md"
    if not article_path.exists():
        return False

    body_md = article_path.read_text(encoding="utf-8")
    if _SPLICED_MARKER in body_md:
        return False

    found = sorted(
        ((int(n), caption) for caption, n in _CANONICAL_INLINE_IMAGE_RE.findall(canonical_md)),
        key=lambda pair: pair[0],
    )
    if not found:
        return False
    images = [(caption, f"../images/inline-{n}.png") for n, caption in found]

    spliced = splice_inline_images(body_md, images)
    _write_atomic(article_path, spliced)
    return True
