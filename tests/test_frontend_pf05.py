"""Article-first workspace contracts for PF-05.

These tests deliberately inspect the shipped Vue source rather than duplicating
the browser.  The browser scenario remains the acceptance test; these guard
against regressing the user-visible vocabulary between builds.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / "frontend/src/views/ArticleWorkspace.vue"


def test_article_workspace_is_article_first_and_hides_legacy_cockpit_language() -> None:
    source = VIEW.read_text(encoding="utf-8")

    for contract in (
        'aria-label="文章工作区"',
        'aria-label="文章标题"',
        'renderMarkdown',
        '编辑 Markdown',
        '保存修改',
        '资料与版本',
        '图片详情',
    ):
        assert contract in source

    for legacy_term in ("视觉圣经", "审批 actor", "stale", "创作流程", "内部 ID"):
        assert legacy_term not in source


def test_article_workspace_preserves_editing_on_save_failure_and_refreshes_saved_versions() -> None:
    source = VIEW.read_text(encoding="utf-8")

    for contract in (
        'lastSaved',
        'saveStatus',
        '保存失败，内容仍在编辑器中',
        '`/projects/${id.value}/master`',
        '`/projects/${id.value}/master/versions/${version}/restore`',
        'restoreVersion',
    ):
        assert contract in source


def test_article_workspace_treats_secondary_data_failures_as_local_and_keeps_article_open() -> None:
    source = VIEW.read_text(encoding="utf-8")
    shell = (ROOT / "frontend/src/layouts/AppShell.vue").read_text(encoding="utf-8")

    for contract in (
        "loadOptionalContext",
        "Promise.allSettled",
        "secondaryError",
        "文章仍可阅读和编辑",
    ):
        assert contract in source
    assert "route.path !== '/' && !route.path.startsWith('/projects/')" in shell


def test_article_workspace_keeps_image_actions_local_to_article() -> None:
    source = VIEW.read_text(encoding="utf-8")

    for contract in (
        'replaceImage',
        'editImage',
        'removeImage',
        'viewImageDetails',
        '`/projects/${id.value}/visuals/assets/${asset.id}/select`',
        '`/projects/${id.value}/visuals/assets/edit`',
        '图片暂时无法加载',
    ):
        assert contract in source
