"""tests/test_mdrender.py — 覆盖 mdrender.md_to_html（32 行零副作用工具，9%→100%）。

TECH_SPEC §10 注释："极简 markdown → HTML（标题/段落/列表）"，webui 内容详情页专用。
"""
from __future__ import annotations

from pipeline.webui.mdrender import esc, md_to_html


class TestEsc:
    def test_ampersand(self):
        assert esc("a & b") == "a &amp; b"

    def test_lt_gt(self):
        assert esc("<script>") == "&lt;script&gt;"

    def test_quote_double(self):
        assert esc('"hello"') == "&quot;hello&quot;"

    def test_quote_single(self):
        assert esc("it's") == "it&#39;s"

    def test_all_combined(self):
        assert esc('a & <b class="x">\'y\'') == "a &amp; &lt;b class=&quot;x&quot;&gt;&#39;y&#39;"


class TestMdToHtmlEmpty:
    def test_empty_string(self):
        assert md_to_html("") == ""

    def test_only_blank_lines(self):
        assert md_to_html("\n\n\n") == ""


class TestMdToHtmlHeadings:
    def test_h1(self):
        assert md_to_html("# Title") == "<h1>Title</h1>"

    def test_h2(self):
        assert md_to_html("## Subtitle") == "<h2>Subtitle</h2>"

    def test_h1_then_paragraph(self):
        assert md_to_html("# Title\n\nBody") == "<h1>Title</h1>\n<p>Body</p>"

    def test_escapes_html_in_heading(self):
        assert md_to_html("# A & B") == "<h1>A &amp; B</h1>"


class TestMdToHtmlParagraphs:
    def test_single_paragraph(self):
        assert md_to_html("hello world") == "<p>hello world</p>"

    def test_two_paragraphs(self):
        assert md_to_html("a\n\nb") == "<p>a</p>\n<p>b</p>"

    def test_escapes_html_in_paragraph(self):
        assert md_to_html("a < b") == "<p>a &lt; b</p>"


class TestMdToHtmlLists:
    def test_single_item(self):
        assert md_to_html("- one") == "<ul>\n<li>one</li>\n</ul>"

    def test_three_items(self):
        out = md_to_html("- a\n- b\n- c")
        assert out == "<ul>\n<li>a</li>\n<li>b</li>\n<li>c</li>\n</ul>"

    def test_list_then_blank_then_list_closes(self):
        # 两个 list 之间空行：第一个 list 闭合，第二个 list 打开
        out = md_to_html("- a\n\n- b")
        assert out == "<ul>\n<li>a</li>\n</ul>\n<ul>\n<li>b</li>\n</ul>"

    def test_list_then_paragraph_closes_list(self):
        out = md_to_html("- a\n\nparagraph")
        assert out == "<ul>\n<li>a</li>\n</ul>\n<p>paragraph</p>"

    def test_list_then_heading_closes_list(self):
        out = md_to_html("- a\n# Title")
        assert out == "<ul>\n<li>a</li>\n</ul>\n<h1>Title</h1>"

    def test_list_then_h2_closes_list(self):
        out = md_to_html("- a\n## Sub")
        assert out == "<ul>\n<li>a</li>\n</ul>\n<h2>Sub</h2>"

    def test_list_directly_followed_by_paragraph_closes_list(self):
        # 列表项后无空行直接接段落（else 分支里的 in_ul 闭合）
        out = md_to_html("- a\npara")
        assert out == "<ul>\n<li>a</li>\n</ul>\n<p>para</p>"

    def test_heading_inside_list_closes_list(self):
        # H1 在 list 之后（H1 分支里的 in_ul 闭合）
        out = md_to_html("- a\n# X")
        assert out == "<ul>\n<li>a</li>\n</ul>\n<h1>X</h1>"

    def test_escapes_html_in_item(self):
        assert md_to_html("- <x>") == "<ul>\n<li>&lt;x&gt;</li>\n</ul>"


class TestMdToHtmlImages:
    def test_image_line_no_base_url(self):
        out = md_to_html("![封面](images/inline-1.png)")
        assert out == '<img src="images/inline-1.png" alt="封面">'

    def test_image_line_with_base_url(self):
        out = md_to_html(
            "![封面](images/inline-1.png)",
            image_base_url="/output/2026-07-05/c_001/",
        )
        assert out == '<img src="/output/2026-07-05/c_001/images/inline-1.png" alt="封面">'

    def test_image_closes_open_list(self):
        out = md_to_html("- a\n![x](img.png)")
        assert out == '<ul>\n<li>a</li>\n</ul>\n<img src="img.png" alt="x">'

    def test_image_mixed_with_paragraphs(self):
        md = "第一段。\n\n![说明文字](images/inline-1.png)\n\n第二段。"
        out = md_to_html(md, image_base_url="/output/2026-07-05/c_001/")
        assert out == (
            "<p>第一段。</p>\n"
            '<img src="/output/2026-07-05/c_001/images/inline-1.png" alt="说明文字">\n'
            "<p>第二段。</p>"
        )

    def test_image_alt_escaped(self):
        out = md_to_html("![a & <b>](x.png)")
        assert out == '<img src="x.png" alt="a &amp; &lt;b&gt;">'

    def test_image_path_escaped(self):
        out = md_to_html("![a](x&y.png)")
        assert out == '<img src="x&amp;y.png" alt="a">'


class TestMdToHtmlMixed:
    def test_realistic_doc(self):
        md = (
            "# 标题\n"
            "\n"
            "第一段文字。\n"
            "\n"
            "## 小节\n"
            "\n"
            "- 列表项一\n"
            "- 列表项二 <tag>\n"
            "\n"
            "第二段 & 转义。\n"
        )
        out = md_to_html(md)
        # 不锁具体格式，只验关键节点全部存在且 HTML 安全
        assert "<h1>标题</h1>" in out
        assert "<h2>小节</h2>" in out
        assert "<ul>" in out and "</ul>" in out
        assert "<li>列表项一</li>" in out
        assert "<li>列表项二 &lt;tag&gt;</li>" in out
        assert "<p>第一段文字。</p>" in out
        assert "<p>第二段 &amp; 转义。</p>" in out
