"""Review regressions: resume must preserve edits and check actual saved content."""
from unittest.mock import Mock

import pytest

from pipeline import master_documents, variants
from pipeline.account_authorization import load_quality_results, quality_fingerprint
from pipeline.auto_create import AutoCreateError, run_auto_create
from tests.test_auto_create import NOW, _draft_fn, _interview, _project, _source
from tests.test_ops_runner import _profile


def _prepared(tmp_path):
    root, accounts = tmp_path / 'projects', tmp_path / 'accounts'
    profile = _profile(accounts)
    pid = 'prj_recovery'
    _project(root, autonomy='pack', project_id=pid)
    _interview(pid, root)
    _source(pid, root)
    kwargs = dict(now=NOW, projects_root=root, accounts_root=accounts,
                  account_id=profile.id, visual_fn=lambda pid: None)
    run_auto_create(pid, draft_fn=_draft_fn, score_fn=lambda t, b: ('pass', 8), **kwargs)
    return pid, profile, kwargs


def test_resume_returns_saved_master_without_rewriting(tmp_path):
    pid, profile, kwargs = _prepared(tmp_path)
    master = master_documents.load_master(pid, projects_root=kwargs['projects_root'])
    writer = Mock(side_effect=AssertionError('must not overwrite existing master'))
    scorer = Mock(return_value=('pass', 9))
    result = run_auto_create(pid, draft_fn=writer, score_fn=scorer, **kwargs)
    assert (result.title, result.body) == (master.title, master.body)
    assert not result.created_master
    assert result.created_platforms == ()
    writer.assert_not_called()
    assert scorer.call_count >= 1
    assert master_documents.load_master(pid, projects_root=kwargs['projects_root']) == master
    assert load_quality_results(profile.id, accounts_root=kwargs['accounts_root'])[-1].score == 9


@pytest.mark.parametrize('edited', ['master', 'wechat_mp'])
def test_resume_rejects_bad_saved_content_without_overwriting(tmp_path, edited):
    pid, profile, kwargs = _prepared(tmp_path)
    root = kwargs['projects_root']
    if edited == 'master':
        master_documents.save_manual(pid, title='坏稿', body='未经核实的短文', now=NOW, projects_root=root)
    else:
        variants.save_manual(pid, edited, title='坏稿', summary='短文', body='未经核实的短文',
                             asset_ids=[], now=NOW, projects_root=root)
    before = quality_fingerprint(pid, projects_root=root)
    scorer = Mock(side_effect=lambda t, b: ('fail', 1) if b == '未经核实的短文' else ('pass', 9))
    with pytest.raises(AutoCreateError, match='质量'):
        run_auto_create(pid, score_fn=scorer, **kwargs)
    assert scorer.call_count > 0
    latest = load_quality_results(profile.id, accounts_root=kwargs['accounts_root'])[-1]
    assert latest.verdict == 'fail'
    assert quality_fingerprint(pid, projects_root=root) == before
