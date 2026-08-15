from __future__ import annotations

import json

import pytest

from pipeline import wechat_accounts


def test_list_includes_legacy_main_file(tmp_path):
    creds = tmp_path / "wechat_mp_main.json"
    creds.write_text(json.dumps({"app_id": "wxaaaaaaaaaaaaaa", "app_secret": "secret"}), encoding="utf-8")
    items = wechat_accounts.list_accounts(secrets_root=tmp_path)
    assert len(items) == 1
    assert items[0].id == "main"
    assert items[0].app_id_masked.endswith("aaaa")
    assert "secret" not in items[0].app_id_masked


def test_add_second_account_and_resolve_credentials(tmp_path):
    wechat_accounts.upsert_account(
        account_id="main", label="主号", app_id="wxaaaaaaaaaaaaaa", app_secret="s1",
        secrets_root=tmp_path,
    )
    wechat_accounts.upsert_account(
        account_id="life", label="生活号", app_id="wxbbbbbbbbbbbbbb", app_secret="s2",
        secrets_root=tmp_path,
    )
    items = wechat_accounts.list_accounts(secrets_root=tmp_path)
    assert [item.id for item in items] == ["main", "life"]
    path = wechat_accounts.credentials_path("life", secrets_root=tmp_path)
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored == {"app_id": "wxbbbbbbbbbbbbbb", "app_secret": "s2"}
    assert path.stat().st_mode & 0o777 == 0o600


def test_rejects_unsafe_account_ids(tmp_path):
    with pytest.raises(wechat_accounts.WechatAccountError):
        wechat_accounts.upsert_account(
            account_id="../x", label="坏", app_id="wx1", app_secret="s", secrets_root=tmp_path,
        )


def test_delete_does_not_leave_secret_file(tmp_path):
    wechat_accounts.upsert_account(
        account_id="life", label="生活号", app_id="wxbbbbbbbbbbbbbb", app_secret="s2",
        secrets_root=tmp_path,
    )
    wechat_accounts.delete_account("life", secrets_root=tmp_path)
    assert wechat_accounts.list_accounts(secrets_root=tmp_path) == ()
    assert not (tmp_path / "wechat_mp_life.json").exists()
