from __future__ import annotations

import pytest

from pipeline.account_onboarding import (
    OnboardingError,
    confirm_onboarding,
    load_onboarding,
    propose_onboarding,
)
from pipeline.account_profiles import create_profile, load_profile


NOW = "2026-09-10T05:00:00+00:00"
LATER = "2026-09-10T06:00:00+00:00"


def _profile(root):
    return create_profile(
        platform="wechat_mp", display_name="主号", positioning="待确认",
        audience="待确认", style="待确认", references=(), creation_mode="assisted",
        frequency="off", timezone="Asia/Shanghai", budget_usd=0,
        delivery_target="draft", credentials_ref="secrets/wechat_mp_main.json",
        config_account_id="main", now=NOW, accounts_root=root,
        profile_id="acc_onb00001",
    )


def test_proposal_does_not_write_profile_until_confirm(tmp_path):
    profile = _profile(tmp_path)
    draft = propose_onboarding(
        profile.id,
        interview={"viewpoint": "工程正确不等于产品有用", "reader": "独立开发者", "must_not": "不编经历"},
        sources=("https://example.com/a",),
        now=NOW,
        accounts_root=tmp_path,
        fetch_text=lambda url: "一篇关于验证顺序的文章" if url.endswith("/a") else None,
        complete_json=lambda prompt, **kwargs: {
            "positioning": "验证先于扩张",
            "audience": "独立开发者",
            "style": "克制、可追责",
            "rationale": "根据访谈和已读来源",
        },
    )
    assert draft.confirmed is False
    assert "验证先于扩张" == draft.suggestion["positioning"]
    still = load_profile(profile.id, accounts_root=tmp_path)
    assert still.positioning == "待确认"
    confirmed = confirm_onboarding(
        profile.id, now=LATER, accounts_root=tmp_path,
    )
    assert confirmed.positioning == "验证先于扩张"
    assert load_onboarding(profile.id, accounts_root=tmp_path).confirmed is True


def test_failed_url_is_unread_not_invented(tmp_path):
    profile = _profile(tmp_path)
    draft = propose_onboarding(
        profile.id,
        interview={"viewpoint": "观点", "reader": "读者", "must_not": "禁区"},
        sources=("https://example.com/missing",),
        now=NOW,
        accounts_root=tmp_path,
        fetch_text=lambda url: None,
        complete_json=lambda prompt, **kwargs: {
            "positioning": "待作者补来源",
            "audience": "读者",
            "style": "克制",
            "rationale": "链接不可读",
        },
    )
    assert draft.unread_sources == ("https://example.com/missing",)
    assert draft.read_excerpts == ()


def test_cannot_confirm_without_interview(tmp_path):
    profile = _profile(tmp_path)
    with pytest.raises(OnboardingError, match="interview"):
        propose_onboarding(
            profile.id,
            interview={"viewpoint": "", "reader": "读者", "must_not": "禁区"},
            sources=(),
            now=NOW,
            accounts_root=tmp_path,
            fetch_text=lambda url: "x",
            complete_json=lambda prompt, **kwargs: {},
        )
    with pytest.raises(OnboardingError, match="no onboarding"):
        confirm_onboarding(profile.id, now=LATER, accounts_root=tmp_path)
