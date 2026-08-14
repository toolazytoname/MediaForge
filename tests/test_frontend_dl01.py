"""DL-01 frontend contracts: final reading state and Markdown-first export."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTICLE = ROOT / "frontend/src/views/ArticleWorkspace.vue"


def test_article_workspace_has_final_reading_state_entry() -> None:
    source = ARTICLE.read_text(encoding="utf-8")
    for contract in (
        "确认最终稿",
        "返回编辑",
        "最终阅读",
        "导出 Markdown",
    ):
        assert contract in source


def test_final_reading_shows_article_composition_not_cockpit() -> None:
    source = ARTICLE.read_text(encoding="utf-8")
    assert "finalReading" in source or "isFinal" in source or "viewMode" in source
    # Finished reading must surface title/body hierarchy and author/AI labels.
    for contract in (
        "article-meta",
        "个人创作",
        "AI",
        "aria-label=\"最终文章\"",
    ):
        assert contract in source
    # Must not resurrect the approval cockpit as the primary completion UI.
    assert "重新检查内容包" not in source
    assert "审批导出" not in source


def test_markdown_export_is_primary_and_zip_is_backup_only() -> None:
    source = ARTICLE.read_text(encoding="utf-8")
    assert "导出 Markdown" in source
    assert "/export/markdown" in source
    # ZIP may exist as secondary backup, never as the sole primary CTA label.
    if "导出 ZIP" in source or "ZIP" in source:
        assert "下载与备份" in source or "备份" in source
    assert source.index("导出 Markdown") < source.index("ZIP") if "ZIP" in source else True


def test_final_reading_keeps_safe_markdown_renderer() -> None:
    source = ARTICLE.read_text(encoding="utf-8")
    assert "renderMarkdown" in source
    assert "finalArticleHtml" in source or 'v-html="articleHtml"' in source
    assert "v-html=" in source
