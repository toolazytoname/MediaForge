"""公众号 HTML 后处理器测试（M13，移植自 TrendPublish html-post-processor.ts）。

覆盖：
  - markdown_to_semantic_html：常见 markdown 元素转出语义 HTML
  - postprocess_html：tag→inline style 注入、<div>→<section>、剥离 script/style/class
  - markdown_to_wechat_html：端到端组合，不崩溃
"""
from __future__ import annotations

import pytest

from pipeline.creators.wechat_html import (
    markdown_to_semantic_html,
    markdown_to_wechat_html,
    postprocess_html,
)


class TestMarkdownToSemanticHtml:
    def test_heading_renders_h_tag(self):
        html = markdown_to_semantic_html("# 标题一\n\n## 标题二")
        assert "<h1>标题一</h1>" in html
        assert "<h2>标题二</h2>" in html

    def test_paragraph_and_bold(self):
        html = markdown_to_semantic_html("这是**加粗**文本")
        assert "<p>" in html
        assert "<strong>加粗</strong>" in html

    def test_link_and_image(self):
        html = markdown_to_semantic_html("[链接](https://example.com) ![alt](https://example.com/a.png)")
        assert '<a href="https://example.com">链接</a>' in html
        assert '<img alt="alt" src="https://example.com/a.png"' in html

    def test_code_block(self):
        html = markdown_to_semantic_html("```python\nprint(1)\n```")
        assert "<pre>" in html
        assert "<code" in html

    def test_table(self):
        md = "| A | B |\n| --- | --- |\n| 1 | 2 |\n"
        html = markdown_to_semantic_html(md)
        assert "<table>" in html

    def test_blockquote(self):
        html = markdown_to_semantic_html("> 引用文字")
        assert "<blockquote>" in html


class TestPostprocessHtml:
    def test_heading_gets_inline_style(self):
        out = postprocess_html("<h1>标题</h1>")
        assert "<h1 style=" in out
        assert "标题</h1>" in out

    def test_paragraph_gets_inline_style(self):
        out = postprocess_html("<p>正文</p>")
        assert "<p style=" in out

    def test_div_becomes_section(self):
        out = postprocess_html("<div>内容</div>")
        assert "<section" in out
        assert "<div" not in out
        assert "</div>" not in out

    def test_script_tag_stripped_entirely(self):
        out = postprocess_html("<p>正文</p><script>alert(1)</script>")
        assert "script" not in out
        assert "alert" not in out

    def test_style_tag_stripped_entirely(self):
        out = postprocess_html("<style>.x{color:red}</style><p>正文</p>")
        assert "<style" not in out
        assert "color:red" not in out

    def test_class_attribute_stripped(self):
        out = postprocess_html('<p class="foo">正文</p>')
        assert 'class=' not in out

    def test_img_gets_inline_style_and_keeps_src(self):
        out = postprocess_html('<img src="https://x.com/a.png" alt="a">')
        assert 'src="https://x.com/a.png"' in out
        assert "style=" in out

    def test_link_gets_inline_style_and_keeps_href(self):
        out = postprocess_html('<a href="https://x.com">链接</a>')
        assert 'href="https://x.com"' in out
        assert "style=" in out

    def test_table_cells_get_border_style(self):
        out = postprocess_html("<table><tr><th>A</th></tr><tr><td>1</td></tr></table>")
        assert "<th style=" in out
        assert "<td style=" in out

    def test_code_inside_pre_uses_transparent_background(self):
        out = postprocess_html("<pre><code>print(1)</code></pre>")
        assert "background:transparent" in out

    def test_inline_code_uses_pill_background(self):
        out = postprocess_html("<p>用 <code>foo()</code> 调用</p>")
        assert "background:#f5f5f5" in out

    def test_unknown_theme_raises(self):
        with pytest.raises(ValueError):
            postprocess_html("<p>x</p>", theme="does-not-exist")

    def test_entity_refs_preserved(self):
        out = postprocess_html("<p>A &amp; B</p>")
        assert "&amp;" in out

    def test_nested_tags_preserve_content(self):
        out = postprocess_html("<p>前<strong>粗体</strong>后</p>")
        assert "前" in out and "粗体" in out and "后" in out


class TestMarkdownToWechatHtml:
    def test_end_to_end_does_not_crash(self):
        md = (
            "# 标题\n\n正文**加粗**与[链接](https://x.com)。\n\n"
            "> 引用\n\n```python\nprint(1)\n```\n\n"
            "- 列表项一\n- 列表项二\n\n"
            "| A | B |\n| --- | --- |\n| 1 | 2 |\n"
        )
        out = markdown_to_wechat_html(md)
        assert "<h1 style=" in out
        assert "<blockquote style=" in out
        assert "<table style=" in out
