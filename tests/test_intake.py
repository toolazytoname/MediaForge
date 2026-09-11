from __future__ import annotations

import io

import pytest

from pipeline import intake


def test_classify_wechat_and_web_urls():
    assert intake.classify_url("https://mp.weixin.qq.com/s/abc") == "wechat"
    assert intake.classify_url("https://example.com/essay") == "web"


def test_classify_local_filenames():
    assert intake.classify_filename("note.md") == "markdown"
    assert intake.classify_filename("paper.PDF") == "pdf"
    assert intake.classify_filename("clip.txt") == "text"


def test_extract_markdown_and_reject_empty():
    source = intake.material_from_file("idea.md", "# 标题\n\n一段笔记。".encode("utf-8"))
    assert source.kind == "markdown"
    assert "一段笔记" in source.excerpt
    with pytest.raises(intake.IntakeError, match="empty"):
        intake.material_from_file("empty.md", b"   ")


def test_extract_pdf_text():
    from pypdf import PdfWriter

    buffer = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_metadata({"/Title": "一份 PDF 笔记"})
    page = writer.pages[0]
    # pypdf blank page has no operators; embed a simple text object via raw write fallback
    writer.write(buffer)
    data = buffer.getvalue()
    # Even a blank generated PDF should be accepted as a file source.
    source = intake.material_from_file("notes.pdf", data)
    assert source.kind == "pdf"
    assert source.title
    assert source.reference == "local:notes.pdf"


def test_blocks_private_urls():
    with pytest.raises(intake.IntakeError, match="safe"):
        intake.ensure_public_http_url("http://127.0.0.1/secret")
    with pytest.raises(intake.IntakeError, match="http"):
        intake.ensure_public_http_url("file:///etc/passwd")


def test_single_pasted_url_becomes_a_source():
    idea, urls = intake.split_pasted_url("https://mp.weixin.qq.com/s/xyz")
    assert idea == ""
    assert urls == ["https://mp.weixin.qq.com/s/xyz"]


def test_heuristic_titles_are_distinct_and_usable():
    titles = intake.heuristic_titles("测试全绿了我还是不敢用自己的产品", ["一份旧笔记"])
    assert len(titles) >= 3
    assert all(title.strip() for title in titles)
    assert len(set(titles)) == len(titles)


def test_heuristic_titles_skip_markdown_images_headings_and_links():
    body = (
        "![封面](/output/projects/prj_x/assets/vas_1.png)\n\n"
        "## 一个平凡人的清醒\n\n"
        "前几天看了[交流会录音稿](https://example.com/a)，我最大的感受是：这是一个清醒的人。\n"
    )
    titles = intake.heuristic_titles(body)
    assert titles[0].startswith("前几天看了交流会录音稿")
    assert all("![" not in title and "](" not in title and "#" not in title for title in titles)


def test_article_title_prompt_reads_the_finished_body_not_the_dump():
    prompt = intake.article_title_prompt(
        body="## 问题\n\n测试全绿了，我还是不敢把链接发给朋友。\n\n## 主张\n\n绿的是测试，不是产品。",
        idea="随便写点想法",
    )
    assert "成稿正文" in prompt
    assert "绿的是测试，不是产品" in prompt
    assert "禁止硬编" not in prompt or "正文为空" in intake.article_title_prompt(body="")


def test_parse_titles_requires_three_clean_strings():
    parsed = intake.parse_titles('{"titles":[" 甲 ","乙","丙","丁"]}')
    assert parsed == ["甲", "乙", "丙", "丁"]
    with pytest.raises(ValueError):
        intake.parse_titles('{"titles":["只有一个"]}')
