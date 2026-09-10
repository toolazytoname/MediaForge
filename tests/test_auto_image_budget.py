"""Paid image calls must be budgeted before dispatch and recoverable afterwards."""
from pathlib import Path
from unittest.mock import Mock

import pytest

from pipeline import db, visuals
from pipeline.account_plans import load_spend
from pipeline.account_profiles import update_profile
from pipeline.auto_create import AutoCreateError, _default_visuals
from pipeline.creators.image_gen import generate_image as real_generate_image
from tests.test_auto_create import NOW, _PNG, _project
from tests.test_ops_runner import _profile


@pytest.fixture
def setup_images(tmp_path, monkeypatch):
    from pipeline.creators import image_gen
    accounts, projects = tmp_path / 'accounts', tmp_path / 'projects'
    profile = _profile(accounts)
    pid = 'prj_imagebudget'
    _project(projects, autonomy='pack', project_id=pid)
    conn = db.connect(tmp_path / 'state.db'); db.init_db(conn); conn.close()
    monkeypatch.setattr('pipeline.webui.deps.get_conn', lambda: db.connect(tmp_path / 'state.db'))
    monkeypatch.setattr(image_gen, '_PROVIDER', Mock(_model='image-01'))
    def generate(prompt, *, out_path, **kwargs):
        path = Path(out_path); path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(_PNG)
        return Mock(model='image-01')
    generator = Mock(side_effect=generate)
    monkeypatch.setattr(image_gen, 'generate_image', generator)
    kwargs = dict(now=NOW, projects_root=projects, accounts_root=accounts, account_id=profile.id)
    return pid, profile, kwargs, generator


def test_insufficient_account_budget_never_calls_image_provider(setup_images):
    pid, profile, kwargs, generator = setup_images
    update_profile(profile, now=NOW, accounts_root=kwargs['accounts_root'], budget_usd=0.001)
    with pytest.raises(AutoCreateError, match='budget'):
        _default_visuals(pid, **kwargs)
    generator.assert_not_called()
    assert load_spend(profile.id, accounts_root=kwargs['accounts_root']) == 0
    assert not visuals.load_visuals(pid, projects_root=kwargs['projects_root']).assets


def test_unpriced_image_never_calls_provider(setup_images, monkeypatch):
    pid, profile, kwargs, generator = setup_images
    monkeypatch.setattr('pipeline.creators.image_gen._PROVIDER', Mock(_model='unpriced-model'))
    with pytest.raises(AutoCreateError, match='unpriced'):
        _default_visuals(pid, **kwargs)
    generator.assert_not_called()


def test_generated_image_survives_asset_registration_failure(setup_images, monkeypatch):
    pid, profile, kwargs, generator = setup_images
    original = visuals.record_asset
    monkeypatch.setattr(visuals, 'record_asset', Mock(side_effect=OSError('disk temporarily unavailable')))
    with pytest.raises(AutoCreateError):
        _default_visuals(pid, **kwargs)
    assert generator.call_count == 1
    monkeypatch.setattr(visuals, 'record_asset', original)
    _default_visuals(pid, **kwargs)
    assert generator.call_count == 3  # recovered first PNG, only the other two were generated
    assert len(visuals.load_visuals(pid, projects_root=kwargs['projects_root']).assets) == 3
    assert load_spend(profile.id, accounts_root=kwargs['accounts_root']) == pytest.approx(0.009)


def test_unknown_image_result_is_not_automatically_repeated(setup_images):
    pid, profile, kwargs, generator = setup_images
    generator.side_effect = TimeoutError('provider may have charged')
    with pytest.raises(AutoCreateError):
        _default_visuals(pid, **kwargs)
    with pytest.raises(AutoCreateError):
        _default_visuals(pid, **kwargs)
    assert generator.call_count == 1


def test_ops_disables_provider_retries_for_unknown_paid_calls(setup_images, monkeypatch):
    from pipeline.creators import image_gen
    pid, profile, kwargs, generator = setup_images
    monkeypatch.setattr(image_gen, 'generate_image', real_generate_image)
    provider = Mock(_model='image-01')
    provider.call.side_effect = image_gen.RetryableError('connection lost after send')
    monkeypatch.setattr(image_gen, '_PROVIDER', provider)
    with pytest.raises(AutoCreateError):
        _default_visuals(pid, **kwargs)
    assert provider.call.call_count == 1


def test_reservations_prevent_other_requests_spending_same_budget(setup_images):
    from pipeline.account_budget import reserve_spend, settle_spend, reserved_spend
    pid, profile, kwargs, generator = setup_images
    accounts = kwargs['accounts_root']
    update_profile(profile, now=NOW, accounts_root=accounts, budget_usd=0.003)
    reserve_spend(profile.id, token='one', amount=0.003, now=NOW, accounts_root=accounts)
    reserve_spend(profile.id, token='one', amount=0.003, now=NOW, accounts_root=accounts)
    with pytest.raises(ValueError, match='budget'):
        reserve_spend(profile.id, token='two', amount=0.003, now=NOW, accounts_root=accounts)
    assert reserved_spend(profile.id, accounts_root=accounts) == 0.003
    settle_spend(profile.id, token='one', now=NOW, accounts_root=accounts)
    settle_spend(profile.id, token='one', now=NOW, accounts_root=accounts)
    assert load_spend(profile.id, accounts_root=accounts) == 0.003
    assert reserved_spend(profile.id, accounts_root=accounts) == 0


def test_global_budget_rejection_releases_reservation_for_later_retry(setup_images):
    from pipeline.account_budget import reserved_spend
    from pipeline.utils.errors import BudgetExceeded
    pid, profile, kwargs, generator = setup_images
    original = generator.side_effect
    generator.side_effect = BudgetExceeded(stage='create_image', used_usd=1, limit_usd=1)
    with pytest.raises(AutoCreateError):
        _default_visuals(pid, **kwargs)
    assert reserved_spend(profile.id, accounts_root=kwargs['accounts_root']) == 0
    generator.side_effect = original
    _default_visuals(pid, **kwargs)
    assert load_spend(profile.id, accounts_root=kwargs['accounts_root']) == pytest.approx(0.009)
