"""微信公众号排版 HTML 生成（M13：官方 API 草稿箱）。

移植来源：TrendPublish `src/features/weixin-article/rendering/dynamic/
html-post-processor.ts`（231 行，正则/字符串清洗，因 Deno 端无 DOM）。
本模块用标准库 html.parser.HTMLParser 重写同一套 tag→inline-style
映射（标题/段落/代码/引用/列表/链接/图片/表格/分隔线），保留其
"清爽"主题配色。不移植 uploadContentImage 相关的正文图片替换逻辑
（v1 范围裁剪，见 evaluation-notes.md §1）。
"""
from __future__ import annotations

import html as html_mod
from html.parser import HTMLParser

import markdown as markdown_lib

_MARKDOWN_EXTENSIONS = ["extra", "sane_lists"]

_VOID_TAGS = {"img", "br", "hr", "input", "meta", "link"}
_SKIP_TAGS = {"script", "style"}
_TAG_RENAME = {"div": "section"}

# tag → 内联样式；"清爽" 主题，微信公众号编辑器常见排版风格
_THEME_STYLES: dict[str, dict[str, str]] = {
    "elegant": {
        "h1": "font-size:22px;font-weight:bold;color:#1a1a1a;margin:24px 0 12px;"
              "padding-bottom:8px;border-bottom:2px solid #07C160;",
        "h2": "font-size:20px;font-weight:bold;color:#1a1a1a;margin:22px 0 10px;",
        "h3": "font-size:18px;font-weight:bold;color:#333333;margin:20px 0 8px;",
        "h4": "font-size:16px;font-weight:bold;color:#333333;margin:18px 0 8px;",
        "p": "font-size:15px;line-height:1.75;color:#333333;margin:10px 0;"
             "letter-spacing:0.05em;",
        "blockquote": "border-left:4px solid #07C160;background:#f7f7f7;"
                       "padding:8px 16px;margin:12px 0;color:#666666;font-style:italic;",
        "code": "background:#f5f5f5;color:#c7254e;padding:2px 4px;border-radius:3px;"
                "font-family:Menlo,Consolas,monospace;font-size:14px;",
        "pre": "background:#282c34;color:#abb2bf;padding:16px;border-radius:6px;"
               "overflow-x:auto;margin:12px 0;font-family:Menlo,Consolas,monospace;"
               "font-size:13px;line-height:1.5;",
        "ul": "padding-left:24px;margin:10px 0;",
        "ol": "padding-left:24px;margin:10px 0;",
        "li": "margin:6px 0;line-height:1.75;color:#333333;",
        "a": "color:#07C160;text-decoration:none;",
        "img": "max-width:100%;display:block;margin:12px auto;border-radius:4px;",
        "table": "border-collapse:collapse;width:100%;margin:12px 0;",
        "th": "border:1px solid #e0e0e0;padding:8px 12px;font-size:14px;"
              "background:#f7f7f7;font-weight:bold;",
        "td": "border:1px solid #e0e0e0;padding:8px 12px;font-size:14px;",
        "hr": "border:none;border-top:1px solid #e0e0e0;margin:20px 0;",
    }
}

# <pre><code> 内的代码块沿用 pre 的深色背景，不再叠加行内 code 的浅底药丸样式
_PRE_CODE_STYLE = (
    "background:transparent;color:inherit;padding:0;"
    "font-family:Menlo,Consolas,monospace;"
)


class _WechatStyleTransformer(HTMLParser):
    """html.parser 版 tag→style 重写器（TrendPublish 用正则字符串替换，
    这里用 stdlib HTMLParser 做 tag 级别重写，避免正则误伤嵌套标签）。"""

    def __init__(self, *, theme: str) -> None:
        super().__init__(convert_charrefs=False)
        if theme not in _THEME_STYLES:
            raise ValueError(f"unknown wechat html theme: {theme!r}")
        self._styles = _THEME_STYLES[theme]
        self._out: list[str] = []
        self._skip_depth = 0
        self._tag_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._emit_start(tag, attrs, self_closing=False)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._emit_start(tag, attrs, self_closing=True)

    def _emit_start(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
        *,
        self_closing: bool,
    ) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return

        out_tag = _TAG_RENAME.get(tag, tag)
        attr_map = {k: v for k, v in attrs if k.lower() != "class"}
        style = self._style_for(tag)
        if style:
            existing = attr_map.get("style") or ""
            attr_map["style"] = f"{existing};{style}" if existing else style
        attr_str = "".join(
            f' {k}="{html_mod.escape(v or "", quote=True)}"' for k, v in attr_map.items()
        )
        is_void = self_closing or out_tag in _VOID_TAGS
        closing = " /" if is_void else ""
        self._out.append(f"<{out_tag}{attr_str}{closing}>")
        if not is_void:
            self._tag_stack.append(tag)

    def _style_for(self, tag: str) -> str:
        if tag == "code" and "pre" in self._tag_stack:
            return _PRE_CODE_STYLE
        return self._styles.get(tag, "")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag in _VOID_TAGS:
            return
        if self._tag_stack and self._tag_stack[-1] == tag:
            self._tag_stack.pop()
        out_tag = _TAG_RENAME.get(tag, tag)
        self._out.append(f"</{out_tag}>")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self._out.append(html_mod.escape(data))

    def handle_entityref(self, name: str) -> None:
        if self._skip_depth:
            return
        self._out.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self._skip_depth:
            return
        self._out.append(f"&#{name};")

    def get_html(self) -> str:
        return "".join(self._out)


def markdown_to_semantic_html(markdown_text: str) -> str:
    """canonical/derivative 正文 markdown → 语义化 HTML（未做微信兼容处理）。"""
    return markdown_lib.markdown(markdown_text, extensions=_MARKDOWN_EXTENSIONS)


def postprocess_html(raw_html: str, *, theme: str = "elegant") -> str:
    """对语义 HTML 做微信兼容后处理：

    - <div> → <section>（公众号编辑器对顶层 div 支持不稳定）
    - 剥离 <script>/<style> 整段内容与所有 class 属性（草稿箱审核会拒绝或吞掉）
    - 逐 tag 注入 inline style：h1-h4/p/blockquote/code/pre/ul/ol/li/a/img/table/hr
    """
    transformer = _WechatStyleTransformer(theme=theme)
    transformer.feed(raw_html)
    transformer.close()
    return transformer.get_html()


def markdown_to_wechat_html(markdown_text: str, *, theme: str = "elegant") -> str:
    """便捷组合：markdown_to_semantic_html → postprocess_html。"""
    return postprocess_html(markdown_to_semantic_html(markdown_text), theme=theme)
