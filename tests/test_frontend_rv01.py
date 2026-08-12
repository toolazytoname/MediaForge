from pathlib import Path


def test_article_workspace_has_explicit_whole_article_feedback_entry_and_review_only_notice():
    source = (Path(__file__).parents[1] / "frontend/src/views/ArticleWorkspace.vue").read_text(encoding="utf-8")
    for contract in ("对整篇提意见", "整篇文章", "减少说教感，保留真实失败", "AI 只会生成提案，不会直接改写正式文章", "/article/feedback", "提案已生成，正式文章尚未修改", "重试生成提案"):
        assert contract in source
