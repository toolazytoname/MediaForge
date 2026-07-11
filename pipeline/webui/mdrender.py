"""M3-3 markdown → HTML 极简渲染（拆分自 app.py，控制单文件 ≤400 行）。

仅覆盖内容详情页需要的语法：H1/H2、列表、段落、图片。其他语法（表格/代码块/
链接）不渲染——webui 详情页只展示已生成内容，不需要完整 markdown。

图片语法（M10-11 阶段 G）：整行 `![alt](rel/path)` 渲染为
`<img src="{image_base_url}{rel/path}" alt="{alt}">`，用于内容详情页
canonical 预览的图文混排展示（rel/path 是相对该内容输出目录的路径，
image_base_url 由调用方传入 `/output/.../` 前缀补全）。
"""
from __future__ import annotations

import re

_IMAGE_RE = re.compile(r"^!\[(.*)\]\((.+)\)$")


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
            out.append(f"<h1>{esc(s[2:])}</h1>")
        elif s.startswith("## "):
            if in_ul:
                out.append("</ul>")
                in_ul = False
            out.append(f"<h2>{esc(s[3:])}</h2>")
        elif s.startswith("- "):
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{esc(s[2:])}</li>")
        elif s.strip() == "":
            if in_ul:
                out.append("</ul>")
                in_ul = False
        else:
            if in_ul:
                out.append("</ul>")
                in_ul = False
            out.append(f"<p>{esc(s)}</p>")
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