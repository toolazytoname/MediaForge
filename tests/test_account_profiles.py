from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.account_profiles import (
    AccountProfileError,
    create_profile,
    import_platform_accounts,
    load_profile,
    update_profile,
)
from pipeline.config import AccountAPI, AccountPlaywright, PlatformAPI, PlatformPlaywright


NOW = "2026-09-10T02:00:00+00:00"
LATER = "2026-09-10T03:00:00+00:00"


def _create(root: Path, **overrides):
    payload = dict(
        platform="wechat_mp",
        display_name="主公众号",
        positioning="个人工程判断",
        audience="独立开发者",
        style="克制",
        references=("https://example.com/a",),
        creation_mode="assisted",
        frequency="2/week",
        timezone="Asia/Shanghai",
        budget_usd=5.0,
        delivery_target="draft",
        credentials_ref="secrets/wechat_mp_main.json",
        config_account_id="main",
        now=NOW,
        accounts_root=root,
        profile_id="acc_aaaa1111",
    )
    payload.update(overrides)
    return create_profile(**payload)


def test_create_and_load_round_trip(tmp_path):
    profile = _create(tmp_path)
    assert profile.operations_enabled is False
    assert load_profile(profile.id, accounts_root=tmp_path) == profile


def test_create_refuses_duplicate(tmp_path):
    _create(tmp_path)
    with pytest.raises(AccountProfileError, match="already exists"):
        _create(tmp_path)


def test_rejects_path_traversal_id(tmp_path):
    with pytest.raises(AccountProfileError, match="invalid account id"):
        _create(tmp_path, profile_id="acc_../x")


def test_credentials_ref_must_stay_under_secrets(tmp_path):
    with pytest.raises(AccountProfileError, match="secrets/"):
        _create(tmp_path, credentials_ref="../etc/passwd")
    with pytest.raises(AccountProfileError, match="secrets/"):
        _create(tmp_path, credentials_ref="/secrets/wechat_mp_main.json")


def test_unknown_field_rejected(tmp_path):
    profile = _create(tmp_path)
    path = tmp_path / profile.id / "account.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["secret"] = "nope"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(AccountProfileError, match="missing or unknown"):
        load_profile(profile.id, accounts_root=tmp_path)


def test_update_is_immutable_and_cannot_silently_enable_from_import(tmp_path):
    original = _create(tmp_path)
    updated = update_profile(
        original, now=LATER, accounts_root=tmp_path, display_name="新名字",
    )
    assert original.display_name == "主公众号"
    assert updated.display_name == "新名字"
    assert updated.operations_enabled is False


def test_import_from_config_does_not_enable_operations(tmp_path):
    platforms = {
        "wechat_mp": PlatformAPI(
            kind="api", windows=["09:00-11:00"],
            accounts=[
                AccountAPI(id="main", credentials="secrets/wechat_mp_main.json"),
                AccountAPI(id="alt", credentials="secrets/wechat_mp_alt.json"),
            ],
        ),
        "toutiao": PlatformPlaywright(
            kind="playwright", windows=["10:00-12:00"],
            accounts=[AccountPlaywright(id="main", cookies="secrets/cookies/toutiao_main.json")],
        ),
    }
    imported = import_platform_accounts(platforms, now=NOW, accounts_root=tmp_path)
    assert len(imported) == 3
    assert {item.config_account_id for item in imported} == {"main", "alt"}
    assert all(item.operations_enabled is False for item in imported)
    wechat = [item for item in imported if item.platform == "wechat_mp"]
    assert wechat[0].credentials_ref != wechat[1].credentials_ref
    again = import_platform_accounts(platforms, now=LATER, accounts_root=tmp_path)
    assert {item.id for item in again} == {item.id for item in imported}
