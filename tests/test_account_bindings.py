from __future__ import annotations

import json

import pytest

from pipeline.account_bindings import (
    AccountBindingError,
    bind_project_account,
    load_bindings,
    require_binding,
)
from pipeline.account_profiles import create_profile
from pipeline.projects import create_project


NOW = "2026-09-10T02:00:00+00:00"


def _project(root):
    return create_project(
        title="主题", idea="想法", audience="读者", goal="文章", voice="清晰",
        autonomy="collaborate", now=NOW, project_id="prj_bindtest",
        projects_root=root / "projects",
    )


def _profile(root, *, profile_id="acc_bind0001", platform="wechat_mp", config_account_id="main"):
    return create_profile(
        platform=platform, display_name="号", positioning="定位", audience="读者",
        style="克制", references=(), creation_mode="assisted", frequency="1/week",
        timezone="Asia/Shanghai", budget_usd=1.0, delivery_target="draft",
        credentials_ref="secrets/wechat_mp_main.json",
        config_account_id=config_account_id, now=NOW,
        accounts_root=root / "accounts", profile_id=profile_id,
    )


def test_bind_and_require_round_trip(tmp_path):
    project = _project(tmp_path)
    profile = _profile(tmp_path)
    bindings = bind_project_account(
        project.id, platform="wechat_mp", account_id=profile.id,
        now=NOW, projects_root=tmp_path / "projects",
        accounts_root=tmp_path / "accounts",
    )
    assert len(bindings.items) == 1
    found = require_binding(
        project.id, platform="wechat_mp", account_id=profile.id,
        projects_root=tmp_path / "projects",
    )
    assert found.account_id == profile.id
    assert load_bindings(project.id, projects_root=tmp_path / "projects") == bindings


def test_missing_binding_rejects_delivery_identity(tmp_path):
    _project(tmp_path)
    _profile(tmp_path)
    with pytest.raises(AccountBindingError, match="not bound"):
        require_binding(
            "prj_bindtest", platform="wechat_mp", account_id="acc_bind0001",
            projects_root=tmp_path / "projects",
        )


def test_wrong_account_does_not_fall_back(tmp_path):
    _project(tmp_path)
    first = _profile(tmp_path, profile_id="acc_one00001", config_account_id="main")
    second = _profile(
        tmp_path, profile_id="acc_two00002", config_account_id="alt",
    )
    bind_project_account(
        "prj_bindtest", platform="wechat_mp", account_id=first.id,
        now=NOW, projects_root=tmp_path / "projects",
        accounts_root=tmp_path / "accounts",
    )
    with pytest.raises(AccountBindingError, match="does not match"):
        require_binding(
            "prj_bindtest", platform="wechat_mp", account_id=second.id,
            projects_root=tmp_path / "projects",
        )


def test_cannot_bind_missing_profile(tmp_path):
    _project(tmp_path)
    with pytest.raises(AccountBindingError, match="account not found"):
        bind_project_account(
            "prj_bindtest", platform="wechat_mp", account_id="acc_missing1",
            now=NOW, projects_root=tmp_path / "projects",
            accounts_root=tmp_path / "accounts",
        )


def test_unknown_binding_fields_rejected(tmp_path):
    _project(tmp_path)
    profile = _profile(tmp_path)
    bind_project_account(
        "prj_bindtest", platform="wechat_mp", account_id=profile.id,
        now=NOW, projects_root=tmp_path / "projects",
        accounts_root=tmp_path / "accounts",
    )
    path = tmp_path / "projects" / "prj_bindtest" / "account_binding.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["extra"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(AccountBindingError, match="missing or unknown"):
        load_bindings("prj_bindtest", projects_root=tmp_path / "projects")
