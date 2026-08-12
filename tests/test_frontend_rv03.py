from pathlib import Path


def test_review_diff_uses_text_nodes_not_unsafe_html_and_keeps_markdown_lines_legible():
    source = (Path(__file__).parents[1] / "frontend/src/views/ArticleWorkspace.vue").read_text(encoding="utf-8")
    assert "lineDiff(master.value.body, proposalBody.value)" in source
    assert "<code>{{ row.text || ' ' }}</code>" in source
    assert "v-html=\"proposal" not in source
    assert "建议标题（可手动调整）" in source


def test_line_diff_uses_lcs_for_add_remove_and_unchanged_markdown_lines():
    source = (Path(__file__).parents[1] / "frontend/src/utils/articleDiff.ts").read_text(encoding="utf-8")
    for contract in ("type DiffRow", "kind: 'same' | 'add' | 'remove'", "matrix[i + 1][j + 1]", "left[i] === right[j]"):
        assert contract in source
