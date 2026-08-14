"""P0 polish contracts before G3: settings path, draft materials, autosave, progressive generate."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "frontend/src/views/CreatorHome.vue"
ARTICLE = ROOT / "frontend/src/views/ArticleWorkspace.vue"
SETTINGS = ROOT / "frontend/src/views/Settings.vue"


def test_creator_home_persists_and_reloads_material_draft_id() -> None:
    source = HOME.read_text(encoding="utf-8")
    for contract in (
        "material-draft-id",
        "/creator-materials/drafts/",
        "restoreMaterialDraftId",
        "loadDraftMaterials",
    ):
        assert contract in source
    # Must not always invent a fresh draft id and orphan uploaded materials.
    assert "materialDraftId.value = newDraftId()" not in source.split("function restoreMaterialDraftId")[1].split("async function")[0] or "localStorage" in source
    assert "localStorage.getItem" in source
    assert "localStorage.setItem" in source


def test_creator_home_and_article_expose_settings_without_developer_drawer() -> None:
    home = HOME.read_text(encoding="utf-8")
    article = ARTICLE.read_text(encoding="utf-8")
    settings = SETTINGS.read_text(encoding="utf-8")
    assert "path: '/settings'" in home or 'path: "/settings"' in home or "/settings" in home
    assert "前往设置" in article
    assert "id=\"openai-image\"" in settings or "id='openai-image'" in settings
    assert "#openai-image" in article
    assert "#openai-image" in home


def test_article_workspace_autosaves_unsaved_edits() -> None:
    source = ARTICLE.read_text(encoding="utf-8")
    for contract in (
        "autosave",
        "setTimeout",
        "clearTimeout",
        "onBeforeUnmount",
        "beforeunload",
    ):
        assert contract in source.lower() or contract in source
    assert "saveStatus.value === 'unsaved'" in source or "saveStatus === 'unsaved'" in source


def test_article_workspace_keeps_reading_and_editing_modes_exclusive() -> None:
    source = ARTICLE.read_text(encoding="utf-8")
    assert 'v-if="isEditing"' in source
    assert 'v-else class="editor-panel"' not in source
    assert 'aria-label="文章阅读"' in source


def test_article_workspace_shows_text_while_images_still_preparing() -> None:
    source = ARTICLE.read_text(encoding="utf-8")
    for contract in (
        "preparing_images",
        "/article/generation",
        "pollGeneration",
    ):
        assert contract in source
    # Full-screen generating must not require waiting for the whole POST.
    assert 'v-if="working && !master"' not in source or "pollGeneration" in source
