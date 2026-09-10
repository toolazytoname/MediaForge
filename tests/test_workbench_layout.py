from pathlib import Path


def test_project_workbench_keeps_article_center_and_collapsible_extras():
    source = Path("frontend/src/views/Projects.vue").read_text(encoding="utf-8")
    assert 'data-testid="article-center"' in source
    assert 'header="资料"' in source
    assert 'header="配图"' in source
    assert 'header="微信与头条稿"' in source
    assert 'class="extra-panels"' in source
    assert "接受为新版本" in source
    assert "恢复为新版本" in source
    assert "创建微信" in source or "wechat_mp" in source
    assert "toutiao" in source
