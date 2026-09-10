from __future__ import annotations

import pytest

from pipeline.account_authorization import (
    AuthorizationError,
    assert_account_may_deliver,
    record_quality_result,
    save_authorization,
)
from pipeline.account_bindings import bind_project_account
from pipeline.account_profiles import create_profile
from pipeline.projects import create_project


NOW = "2026-09-10T10:00:00+00:00"


def _setup(tmp_path, *, autonomy="collaborate"):
    projects = tmp_path / "projects"
    accounts = tmp_path / "accounts"
    project = create_project(
        title="主题", idea="想法", audience="读者", goal="文章", voice="清晰",
        autonomy=autonomy, now=NOW, project_id="prj_auth0001",
        projects_root=projects,
    )
    profile = create_profile(
        platform="wechat_mp", display_name="主号", positioning="定位",
        audience="读者", style="克制", references=(), creation_mode="auto",
        frequency="2/week", timezone="Asia/Shanghai", budget_usd=1,
        delivery_target="draft", credentials_ref="secrets/wechat_mp_main.json",
        config_account_id="main", now=NOW, accounts_root=accounts,
        profile_id="acc_auth0001",
    )
    bind_project_account(
        project.id, platform="wechat_mp", account_id=profile.id, now=NOW,
        projects_root=projects, accounts_root=accounts,
    )
    return project, profile, projects, accounts


def test_pack_autonomy_cannot_authorize_direct(tmp_path):
    project, profile, projects, accounts = _setup(tmp_path, autonomy="pack")
    save_authorization(
        profile.id, actor="lazy", now=NOW, accounts_root=accounts,
        operations_enabled=True, allow_draft=True, allow_direct=True,
        quality_floor=6.0,
    )
    with pytest.raises(AuthorizationError, match="不能直发"):
        assert_account_may_deliver(
            project.id, platform="wechat_mp", account_id=profile.id, mode="direct",
            path="auto", projects_root=projects, accounts_root=accounts,
        )


def test_operations_disabled_blocks_auto_delivery(tmp_path):
    project, profile, projects, accounts = _setup(tmp_path)
    save_authorization(
        profile.id, actor="lazy", now=NOW, accounts_root=accounts,
        operations_enabled=False, allow_draft=True, allow_direct=False,
        quality_floor=0,
    )
    with pytest.raises(AuthorizationError, match="operations"):
        assert_account_may_deliver(
            project.id, platform="wechat_mp", account_id=profile.id, mode="draft",
            path="auto", projects_root=projects, accounts_root=accounts,
        )


def test_human_approval_does_not_travel_to_other_account(tmp_path):
    project, profile, projects, accounts = _setup(tmp_path)
    other = create_profile(
        platform="wechat_mp", display_name="副号", positioning="定位",
        audience="读者", style="克制", references=(), creation_mode="assisted",
        frequency="off", timezone="Asia/Shanghai", budget_usd=0,
        delivery_target="draft", credentials_ref="secrets/wechat_mp_alt.json",
        config_account_id="alt", now=NOW, accounts_root=accounts,
        profile_id="acc_auth0002",
    )
    with pytest.raises(AuthorizationError, match="does not match"):
        assert_account_may_deliver(
            project.id, platform="wechat_mp", account_id=other.id, mode="draft",
            path="human", projects_root=projects, accounts_root=accounts,
        )


def test_auto_delivery_requires_quality_floor(tmp_path):
    project, profile, projects, accounts = _setup(tmp_path)
    save_authorization(
        profile.id, actor="lazy", now=NOW, accounts_root=accounts,
        operations_enabled=True, allow_draft=True, allow_direct=True,
        quality_floor=7.0,
    )
    with pytest.raises(AuthorizationError, match="quality"):
        assert_account_may_deliver(
            project.id, platform="wechat_mp", account_id=profile.id, mode="draft",
            path="auto", projects_root=projects, accounts_root=accounts,
        )
    record_quality_result(
        profile.id, project_id=project.id, score=8.0, verdict="pass",
        now=NOW, accounts_root=accounts,
    )
    assert_account_may_deliver(
        project.id, platform="wechat_mp", account_id=profile.id, mode="draft",
        path="auto", projects_root=projects, accounts_root=accounts,
    )


def test_quality_result_is_machine_only(tmp_path):
    _project, profile, _projects, accounts = _setup(tmp_path)
    result = record_quality_result(
        profile.id, project_id="prj_auth0001", score=7.5, verdict="pass",
        now=NOW, accounts_root=accounts,
    )
    assert result.human_verified is False
    assert result.verdict == "pass"
