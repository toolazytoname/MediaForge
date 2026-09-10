"""DEL-03: Toutiao project draft/direct, cookie isolation, receipts, export is not send."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline import db
from pipeline.account_bindings import bind_project_account
from pipeline.account_profiles import create_profile
from pipeline.config import PublishConfig
from pipeline.delivery.service import create_direct, create_draft, create_export_delivery
from pipeline.publishers.base import AccountConfig, LoginExpired, PostBundle, PublishError, PublishResult
from pipeline.publishers.toutiao import ToutiaoPublisher
from tests.test_delivery_kernel import _complete, _ready

NOW = "2026-09-10T12:30:00+00:00"


def _storage_state(path: Path) -> Path:
    path.write_text(json.dumps({
        "cookies": [{
            "name": "sessionid", "value": path.name, "domain": ".toutiao.com",
            "path": "/", "expires": 9999999999, "httpOnly": True, "secure": True,
            "sameSite": "None",
        }],
        "origins": [],
    }), encoding="utf-8")
    return path


class _ToutiaoStub:
    platform = "toutiao"

    def __init__(self, *, post_id="tt_draft_1", url=None, error: Exception | None = None):
        self.calls = 0
        self.modes: list[str] = []
        self.cookie_paths: list[str] = []
        self.post_id = post_id
        self.url = url
        self.error = error

    def capabilities(self):
        from pipeline.publishers.capabilities import default_capabilities
        return default_capabilities(draft=True, direct=True, detail="test")

    def validate(self, bundle: PostBundle) -> list[str]:
        return []

    def publish(self, bundle, account, dry_run=False) -> PublishResult:
        self.calls += 1
        self.modes.append((bundle.extra or {}).get("delivery_mode") or "draft")
        self.cookie_paths.append(str(account.credentials_path))
        if self.error:
            raise self.error
        if dry_run:
            return PublishResult("dry-toutiao", None, '{"dry_run": true}')
        receipt = {
            "platform": "toutiao",
            "account": account.id,
            "platform_post_id": self.post_id,
            "platform_url": self.url,
            "delivery_mode": self.modes[-1],
        }
        return PublishResult(self.post_id, self.url, json.dumps(receipt, ensure_ascii=False))


def _profile(accounts: Path, *, profile_id: str, target="draft"):
    return create_profile(
        platform="toutiao", display_name="头条号", positioning="定位",
        audience="读者", style="克制", references=(), creation_mode="assisted",
        frequency="off", timezone="Asia/Shanghai", budget_usd=0,
        delivery_target=target, credentials_ref="secrets/cookies/toutiao_main.json",
        config_account_id="main", now=NOW, accounts_root=accounts, profile_id=profile_id,
    )


def _bind(tmp_path, *, target="draft", profile_id="acc_tt000001"):
    root = tmp_path / "projects"
    accounts = tmp_path / "accounts"
    project_id = _ready(root, project_id="prj_ttdeliv")
    _complete(root, project_id)
    profile = _profile(accounts, profile_id=profile_id, target=target)
    bind_project_account(
        project_id, platform="toutiao", account_id=profile.id, now=NOW,
        projects_root=root, accounts_root=accounts,
    )
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    return project_id, profile, root, accounts, conn


def test_missing_cookie_does_not_claim_success(tmp_path: Path) -> None:
    cookies = tmp_path / "missing-toutiao.json"
    body = tmp_path / "toutiao.md"
    body.write_text("正文" * 200, encoding="utf-8")
    called = {"publish": 0}

    def fake_publish(**kw):
        called["publish"] += 1
        return PublishResult("should-not", "https://x", "{}")

    adapter = ToutiaoPublisher(
        cookies_path=cookies,
        health_probe=lambda *a, **k: (200, "https://mp.toutiao.com/", "ok"),
        publish_fn=fake_publish,
    )
    bundle = PostBundle("c1", "标题标题标题", body, (), (), {})
    issues = adapter.validate(bundle)
    assert any("cookies file missing" in item for item in issues)
    with pytest.raises(PublishError, match="cookies"):
        adapter.publish(bundle, AccountConfig("main", cookies), dry_run=False)
    assert called["publish"] == 0


def test_account_storage_is_not_shared(tmp_path: Path) -> None:
    body = tmp_path / "toutiao.md"
    body.write_text("正文" * 200, encoding="utf-8")
    cookie_a = _storage_state(tmp_path / "a.json")
    cookie_b = _storage_state(tmp_path / "b.json")
    seen: list[str] = []

    def fake_publish(**kw):
        seen.append(str(kw["cookies_path"]))
        return PublishResult("id", "https://mp.toutiao.com/x", '{"platform_post_id":"id"}')

    def ok_probe(*a, **k):
        return (200, "https://mp.toutiao.com/", "ok")

    a = ToutiaoPublisher(cookies_path=cookie_a, health_probe=ok_probe, publish_fn=fake_publish)
    b = ToutiaoPublisher(cookies_path=cookie_b, health_probe=ok_probe, publish_fn=fake_publish)
    bundle = PostBundle("c1", "标题标题标题", body, (), (), {})
    a.publish(bundle, AccountConfig("acc_a", cookie_a), dry_run=False)
    b.publish(bundle, AccountConfig("acc_b", cookie_b), dry_run=False)
    assert seen == [str(cookie_a), str(cookie_b)]


def test_login_expired_does_not_retry(tmp_path) -> None:
    project_id, profile, root, accounts, conn = _bind(tmp_path)
    adapter = _ToutiaoStub(error=LoginExpired("toutiao cookie expired"))
    account = AccountConfig(id=profile.id, credentials_path=tmp_path / "a.json")
    cfg = PublishConfig(enabled=True, allowed_platforms=["toutiao"])
    first = create_draft(
        conn, project_id=project_id, deliverable_id="dlv_article_toutiao",
        actor="lazy", adapter=adapter, account=account, publish_config=cfg,
        projects_root=root,
    )
    assert first.attempt.outcome == "failure"
    assert "expired" in (first.attempt.error or "")
    assert adapter.calls == 1
    second = create_draft(
        conn, project_id=project_id, deliverable_id="dlv_article_toutiao",
        actor="lazy", adapter=adapter, account=account, publish_config=cfg,
        projects_root=root,
    )
    assert second.replayed is True
    assert adapter.calls == 1


def test_draft_receipt_fields_enter_attempt(tmp_path) -> None:
    project_id, profile, root, accounts, conn = _bind(tmp_path, target="draft")
    adapter = _ToutiaoStub(post_id="draft_mid_9", url=None)
    account = AccountConfig(id=profile.id, credentials_path=tmp_path / "a.json")
    result = create_draft(
        conn, project_id=project_id, deliverable_id="dlv_article_toutiao",
        actor="lazy", adapter=adapter, account=account,
        publish_config=PublishConfig(enabled=True, allowed_platforms=["toutiao"]),
        projects_root=root,
    )
    assert result.attempt.outcome == "success"
    assert result.attempt.mode == "draft"
    assert result.attempt.platform_post_id == "draft_mid_9"
    assert result.media_id == "draft_mid_9"
    assert "draft_mid_9" in (result.attempt.raw_receipt or "")


def test_direct_receipt_fields_enter_attempt(tmp_path) -> None:
    project_id, profile, root, accounts, conn = _bind(tmp_path, target="direct", profile_id="acc_ttdir001")
    adapter = _ToutiaoStub(post_id="pub_mid_2", url="https://www.toutiao.com/article/2/")
    account = AccountConfig(id=profile.id, credentials_path=tmp_path / "a.json")
    result = create_direct(
        conn, project_id=project_id, deliverable_id="dlv_article_toutiao",
        actor="lazy", adapter=adapter, account=account,
        publish_config=PublishConfig(enabled=True, allowed_platforms=["toutiao"]),
        confirm_token="yes-publish", projects_root=root, accounts_root=accounts,
    )
    assert result.attempt.outcome == "success"
    assert result.attempt.mode == "direct"
    assert result.attempt.platform_post_id == "pub_mid_2"
    assert result.attempt.platform_url == "https://www.toutiao.com/article/2/"


def test_export_is_not_a_platform_send(tmp_path) -> None:
    project_id, _profile, root, _accounts, conn = _bind(tmp_path)
    result = create_export_delivery(
        conn, project_id=project_id, deliverable_id="dlv_article_toutiao",
        actor="lazy", projects_root=root,
    )
    assert result.attempt.mode == "export"
    assert result.attempt.platform_post_id is None
    assert result.export is not None
    assert "no platform receipt" in (result.attempt.raw_receipt or "")


def test_draft_mode_is_passed_to_publish_fn(tmp_path: Path) -> None:
    cookies = _storage_state(tmp_path / "cookies.json")
    body = tmp_path / "toutiao.md"
    body.write_text("正文" * 200, encoding="utf-8")
    captured: dict = {}

    def fake_publish(**kw):
        captured.update(kw)
        return PublishResult("d1", None, '{"platform_post_id":"d1","mode":"draft"}')

    adapter = ToutiaoPublisher(
        cookies_path=cookies,
        health_probe=lambda *a, **k: (200, "https://mp.toutiao.com/", "ok"),
        publish_fn=fake_publish,
    )
    bundle = PostBundle("c1", "标题标题标题", body, (), (), {"delivery_mode": "draft"})
    result = adapter.publish(bundle, AccountConfig("main", cookies), dry_run=False)
    assert captured["delivery_mode"] == "draft"
    assert captured["cookies_path"] == cookies
    assert result.platform_post_id == "d1"
