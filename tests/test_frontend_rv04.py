from pathlib import Path


def _source() -> str:
    return (Path(__file__).parents[1] / "frontend/src/views/ArticleWorkspace.vue").read_text(encoding="utf-8")


def test_review_acceptance_reports_the_new_formal_version_and_returns_to_article():
    source = _source()
    assert "修改已确认，已生成正式版本 v{{ versionNotice.version }}" in source
    assert "versionNotice.value = { kind: 'accepted', version: master.value.version }" in source
    assert "proposalReview.value = null" in source


def test_rejected_proposal_returns_without_changing_the_article_and_stale_copy_offers_recompare():
    source = _source()
    assert "proposalReview.value = null" in source
    assert "这份建议基于修改前的文章，不能直接套用。" in source
    assert "以当前文章重新比较" in source
    assert "function recompareProposal" in source


def test_version_restore_requires_a_confirmation_before_the_restore_request_and_reports_new_version():
    source = _source()
    assert "restoreCandidate.value = version" in source
    assert "确认恢复版本 {{ restoreCandidate }}" in source
    assert "confirmRestoreVersion" in source
    assert "master/versions/${version}/restore" in source
    assert "恢复完成，已生成正式版本 v{{ versionNotice.version }}" in source
    assert "恢复不会覆盖任何历史。" in source


def test_version_history_stays_secondary_and_explains_why_a_version_exists():
    source = _source()
    assert 'title="资料与版本"' in source
    assert "versionReason(version.reason)" in source
