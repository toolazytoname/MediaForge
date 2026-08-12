"""UX-00 article-first prototype contract.

This deliberately tests only the prototype's public interaction vocabulary.  UX-00
is a client-only calibration artifact: it must not introduce a pipeline endpoint,
schema, or publish action just to make a demo clickable.
"""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / "frontend/src/views/CreatorPrototype.vue"


def test_article_first_prototype_covers_required_creator_states() -> None:
    source = VIEW.read_text(encoding="utf-8")

    for visible_contract in (
        "主题和想法",
        "添加参考资料",
        "生成文章",
        "资料暂时无法读取",
        "给这一段提意见",
        "评论所选内容",
        "右键评论",
        "选中一段文字后，可以直接评论",
        "查看修改对比",
        "确认这版",
        "最终阅读",
        "选择账号与发布方式",
        "平台原生定时",
    ):
        assert visible_contract in source


def test_local_comment_is_anchored_to_an_actual_text_selection() -> None:
    source = VIEW.read_text(encoding="utf-8")

    assert "window.getSelection()" in source
    assert "selectionMenu" in source
    assert "@mouseup=\"captureTextSelection\"" in source
    assert "@contextmenu.prevent=\"openContextComment\"" in source


def test_prototype_has_no_pipeline_or_real_publish_request() -> None:
    source = VIEW.read_text(encoding="utf-8")
    assert "axios" not in source
    assert "/api/" not in source
    assert "fetch(" not in source


def test_real_creator_home_has_one_required_semantic_input_and_safe_start_contract() -> None:
    source = (ROOT / "frontend/src/views/CreatorHome.vue").read_text(encoding="utf-8")

    for contract in (
        "把一个想法做成文章",
        "生成文章",
        "/projects/creator-start",
        "localStorage",
        "@keydown.meta.enter.prevent",
        "@keydown.ctrl.enter.prevent",
        ":disabled=\"!canStart || starting\"",
        "最近创作",
        "自动化创作",
    ):
        assert contract in source
    assert "自主程度" not in source
