"""M3-3 markdown → HTML 极简渲染（拆分自 app.py，控制单文件 ≤400 行）。

覆盖内容详情页和平台预览需要的安全子集：H1/H2、列表、段落、图片、引用、
分隔线、粗体和 http(s) 链接。原始 HTML 一律转义；不接受 javascript/data URL。

图片语法（M10-11 阶段 G）：整行 `![alt](rel/path)` 渲染为
`<img src="{image_base_url}{rel/path}" alt="{alt}">`，用于内容详情页
canonical 预览的图文混排展示（rel/path 是相对该内容输出目录的路径，
image_base_url 由调用方传入 `/output/.../` 前缀补全）。
"""
from __future__ import annotations

import re

_IMAGE_RE = re.compile(r"^!\[(.*)\]\((.+)\)$")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_STRONG_RE = re.compile(r"\*\*([^*\n]+)\*\*")


def md_to_html(md: str, image_base_url: str = "") -> str:
    """极简 markdown → HTML（标题/段落/列表/图片）。"""
    lines = md.split("\n")
    out: list[str] = []
    in_ul = False
    for line in lines:
        s = line.rstrip()
        image_match = _IMAGE_RE.match(s.strip())
        if image_match:
            if in_ul:
                out.append("</ul>")
                in_ul = False
            alt, path = image_match.group(1), image_match.group(2)
            out.append(f'<img src="{esc(image_base_url + path)}" alt="{esc(alt)}">')
        elif s.startswith("# "):
            if in_ul:
                out.append("</ul>")
                in_ul = False
            out.append(f"<h1>{inline_md(s[2:])}</h1>")
        elif s.startswith("## "):
            if in_ul:
                out.append("</ul>")
                in_ul = False
            out.append(f"<h2>{inline_md(s[3:])}</h2>")
        elif s.startswith("- "):
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{inline_md(s[2:])}</li>")
        elif s.strip() in {"---", "***"}:
            if in_ul:
                out.append("</ul>")
                in_ul = False
            out.append("<hr>")
        elif s.startswith("> "):
            if in_ul:
                out.append("</ul>")
                in_ul = False
            out.append(f"<blockquote><p>{inline_md(s[2:])}</p></blockquote>")
        elif s.strip() == "":
            if in_ul:
                out.append("</ul>")
                in_ul = False
        else:
            if in_ul:
                out.append("</ul>")
                in_ul = False
            out.append(f"<p>{inline_md(s)}</p>")
    if in_ul:
        out.append("</ul>")
    return "\n".join(out)


def esc(s: str) -> str:
    """HTML 转义（& < > " '）。"""
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def inline_md(s: str) -> str:
    """Render a deliberately small inline subset after escaping all input."""
    placeholders: list[str] = []

    def link(match: re.Match[str]) -> str:
        index = len(placeholders)
        label = _STRONG_RE.sub(r"<strong>\1</strong>", esc(match.group(1)))
        placeholders.append(
            f'<a href="{esc(match.group(2))}" target="_blank" rel="noreferrer">'
            f"{label}</a>"
        )
        return f"\x00LINK{index}\x00"

    linked = _LINK_RE.sub(link, s)
    rendered = _STRONG_RE.sub(r"<strong>\1</strong>", esc(linked))
    for index, value in enumerate(placeholders):
        rendered = rendered.replace(f"\x00LINK{index}\x00", value)
    return rendered
