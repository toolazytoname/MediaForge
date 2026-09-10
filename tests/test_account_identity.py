from __future__ import annotations

from types import SimpleNamespace

import pytest

from pipeline.account_identity import AccountIdentityError, resolve_platform_account
from pipeline.config import AccountAPI, PlatformAPI
from pipeline.scheduler import plan
from pipeline.webui.api.delivery import _adapter_for


def test_resolve_requires_explicit_id_when_multiple_accounts():
    plat = PlatformAPI(
        kind="api", windows=["09:00-11:00"],
        accounts=[
            AccountAPI(id="main", credentials="secrets/wechat_mp_main.json"),
            AccountAPI(id="alt", credentials="secrets/wechat_mp_alt.json"),
        ],
    )
    with pytest.raises(AccountIdentityError, match="account_id required"):
        resolve_platform_account(plat, account_id=None)
    chosen = resolve_platform_account(plat, account_id="alt")
    assert chosen.id == "alt"
    assert chosen.credentials.endswith("wechat_mp_alt.json")


def test_adapter_for_does_not_use_first_account(monkeypatch):
    cfg = SimpleNamespace(
        platforms=SimpleNamespace(
            wechat_mp=PlatformAPI(
                kind="api", windows=["09:00-11:00"],
                accounts=[
                    AccountAPI(id="main", credentials="secrets/wechat_mp_main.json"),
                    AccountAPI(id="alt", credentials="secrets/wechat_mp_alt.json"),
                ],
            )
        )
    )

    class FakeAdapter:
        def __init__(self, account):
            self.account = account

    monkeypatch.setattr(
        "pipeline.webui.api.delivery.get_adapter",
        lambda platform, account=None, config=None: FakeAdapter(account),
    )
    with pytest.raises(AccountIdentityError, match="account_id required"):
        _adapter_for(cfg, "wechat_mp")
    adapter, account = _adapter_for(cfg, "wechat_mp", account_id="alt")
    assert account.id == "alt"
    assert adapter.account.id == "alt"


def test_plan_refuses_first_account_when_multiple_exist():
    from pipeline.models import Content, ContentStatus

    plat = PlatformAPI(
        kind="api", windows=["09:00-11:00"],
        accounts=[
            AccountAPI(id="main", credentials="secrets/a.json"),
            AccountAPI(id="alt", credentials="secrets/b.json"),
        ],
    )
    content = Content(
        id="c_acctest", topic_id="t_acctest", pillar="ai_daily", title="t",
        canonical_path="output/x.md", formats='["wechat_mp"]',
        gate_score_total=20, gate_scores="{}", gate_verdict="ok",
        status=ContentStatus.APPROVED.value,
        created_at="2026-09-10T00:00:00+00:00",
        updated_at="2026-09-10T00:00:00+00:00",
    )
    with pytest.raises(AccountIdentityError, match="account_id required"):
        plan(
            [content], {"wechat_mp": plat}, [], "2026-09-10T01:00:00+00:00",
            min_gap_hours=4, cross_platform_gap_minutes=30,
        )
    result = plan(
        [content], {"wechat_mp": plat}, [], "2026-09-10T01:00:00+00:00",
        min_gap_hours=4, cross_platform_gap_minutes=30,
        account_ids={"wechat_mp": "alt"},
    )
    assert result.publications[0].account_id == "alt"
