"""M3-3 markdown → HTML 极简渲染（拆分自 app.py，控制单文件 ≤400 行）。

仅覆盖内容详情页需要的语法：H1/H2、列表、段落。其他语法（表格/代码块/链接）
不渲染——webui 详情页只展示已生成内容，不需要完整 markdown。
"""
from __future__ import annotations


def md_to_html(md: str) -> str:
    """极简 markdown → HTML（标题/段落/列表）。"""
    lines = md.split("\n")
    out: list[str] = []
    in_ul = False
    for line in lines:
        s = line.rstrip()
        if s.startswith("# "):
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