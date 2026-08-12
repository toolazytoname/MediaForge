from pathlib import Path


def test_article_workspace_offers_selection_and_context_menu_local_feedback_with_safe_annotation_state():
    source = (Path(__file__).parents[1] / "frontend/src/views/ArticleWorkspace.vue").read_text(encoding="utf-8")
    for contract in (
        "评论所选内容", "@contextmenu.prevent=\"openTextAnnotation", "window.getSelection()",
        "/article/annotations/${path}", "局部批注", "已失配", "移除批注",
        "composition", "subject", "fact", "asset_id",
    ):
        assert contract in source
