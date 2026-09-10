"""DEL-02: WeChat multi-account draft/direct, permission gates, unknown receipts."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline import db
from pipeline.account_authorization import record_quality_result, save_authorization
from pipeline.account_bindings import bind_project_account
from pipeline.account_profiles import create_profile
from pipeline.config import AccountAPI, PlatformAPI, PublishConfig
from pipeline.delivery.service import DeliveryError, create_direct, create_draft, verify_direct_receipt
from pipeline.publishers.base import AccountConfig, PostBundle, PublishResult, PublisherAdapter
from pipeline.publishers.wechat_mp import WechatMpPublisher
from pipeline.webui.api import delivery as delivery_api
from tests.test_delivery_kernel import _complete, _ready

NOW = "2026-09-10T12:00:00+00:00"


class _CountingWechat(PublisherAdapter):
    platform = "wechat_mp"

    def __init__(self, *, media_id="media_ok", article_id="article_ok", unknown=False):
        self.calls = 0
        self.modes: list[str] = []
        self.media_id = media_id
        self.article_id = article_id
        self.unknown = unknown
        self.app_id = "unused"

    def capabilities(self):
        from pipeline.publishers.capabilities import default_capabilities
        return default_capabilities(draft=True, direct=True, detail="test")

    def validate(self, bundle: PostBundle) -> list[str]:
        return []

    def publish(self, bundle, account, dry_run=False) -> PublishResult:
        self.calls += 1
        self.modes.append((bundle.extra or {}).get("delivery_mode") or "draft")
        if dry_run:
            return PublishResult("dry", None, '{"dry_run": true}')
        if self.unknown:
            raise self._unknown()
        if (bundle.extra or {}).get("delivery_mode") == "direct":
            return PublishResult(
                self.article_id, "https://mp.weixin.qq.com/s/art",
                json.dumps({"article_id": self.article_id, "publish_status": 0}),
            )
        return PublishResult(self.media_id, None, json.dumps({"draft_media_id": self.media_id}))

    def query_freepublish(self, publish_id: str) -> dict:
        self.calls += 1
        return {"publish_id": publish_id, "publish_status": 0, "article_id": self.article_id}

    def _unknown(self):
        from pipeline.publishers.base import PublishError
        return PublishError("unknown receipt: freepublish still publishing; do not retry publish_id=p_unknown")


def _creds(path: Path, app_id: str) -> Path:
    path.write_text(json.dumps({"app_id": app_id, "app_secret": f"secret-{app_id}"}), encoding="utf-8")
    return path


def _profile(accounts: Path, *, profile_id: str, config_id: str, creds: str, target="direct"):
    return create_profile(
        platform="wechat_mp", display_name=config_id, positioning="定位",
        audience="读者", style="克制", references=(), creation_mode="assisted",
        frequency="off", timezone="Asia/Shanghai", budget_usd=0,
        delivery_target=target, credentials_ref=creds, config_account_id=config_id,
        now=NOW, accounts_root=accounts, profile_id=profile_id,
    )


def _bind_ready(tmp_path, *, target="direct", profile_id="acc_direct01"):
    root = tmp_path / "projects"
    accounts = tmp_path / "accounts"
    project_id = _ready(root, project_id="prj_wxdirect")
    _complete(root, project_id)
    creds = _creds(tmp_path / "wechat_mp_main.json", "wxMAIN")
    profile = _profile(
        accounts, profile_id=profile_id, config_id="main",
        creds=str(Path("secrets") / creds.name), target=target,
    )
    bind_project_account(
        project_id, platform="wechat_mp", account_id=profile.id, now=NOW,
        projects_root=root, accounts_root=accounts,
    )
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    return project_id, profile, root, accounts, conn


def test_two_wechat_publishers_do_not_share_app_credentials(tmp_path: Path) -> None:
    content_dir = tmp_path / "content"
    wechat_dir = content_dir / "wechat_mp"
    wechat_dir.mkdir(parents=True)
    (wechat_dir / "meta.json").write_text(
        json.dumps({"title": "标题", "digest": "摘要"}, ensure_ascii=False), encoding="utf-8",
    )
    (wechat_dir / "article.md").write_text("# 标题\n\n正文", encoding="utf-8")
    (content_dir / "cover.png").write_bytes(b"\x89PNG\r\n" + b"x" * 80)

    seen: list[str] = []

    def make(app_id: str) -> WechatMpPublisher:
        def fake_get(url, *, params, timeout=30.0):
            seen.append(params["appid"])
            return {"access_token": f"tok-{params['appid']}", "expires_in": 7200}

        def fake_upload(url, *, params, file_path, timeout=60.0):
            if url.endswith("/media/uploadimg"):
                return {"url": "https://mmbiz.qpic.cn/x"}
            return {"media_id": f"cover-{app_id}"}

        def fake_post(url, *, params, json_body, timeout=30.0):
            return {"media_id": f"draft-{app_id}"}

        return WechatMpPublisher(
            app_id=app_id, app_secret=f"s-{app_id}",
            http_get=fake_get, http_post=fake_post, http_upload=fake_upload,
        )

    bundle = PostBundle("c1", "t", content_dir / "canonical.md", (), (), {})
    a = make("wxAAA")
    b = make("wxBBB")
    ra = a.publish(bundle, AccountConfig("acc_a", tmp_path / "a.json"), dry_run=False)
    rb = b.publish(bundle, AccountConfig("acc_b", tmp_path / "b.json"), dry_run=False)
    assert ra.platform_post_id == "draft-wxAAA"
    assert rb.platform_post_id == "draft-wxBBB"
    assert seen == ["wxAAA", "wxBBB"]
    assert a._app_id != b._app_id


def test_adapter_for_isolates_named_account_credentials(tmp_path: Path) -> None:
    a = _creds(tmp_path / "wechat_mp_a.json", "wxAAA")
    b = _creds(tmp_path / "wechat_mp_b.json", "wxBBB")
    plat = PlatformAPI(
        kind="api", windows=["09:00-11:00"],
        accounts=[
            AccountAPI(id="main", credentials=str(a)),
            AccountAPI(id="alt", credentials=str(b)),
        ],
    )
    cfg = SimpleNamespace(platforms=SimpleNamespace(wechat_mp=plat))
    adapter_a, acc_a = delivery_api._adapter_for(cfg, "wechat_mp", account_id="main")
    adapter_b, acc_b = delivery_api._adapter_for(cfg, "wechat_mp", account_id="alt")
    assert acc_a.id == "main" and acc_b.id == "alt"
    assert adapter_a._app_id == "wxAAA"
    assert adapter_b._app_id == "wxBBB"
    assert Path(acc_a.credentials_path) != Path(acc_b.credentials_path)


def test_missing_direct_permission_does_not_call_publisher(tmp_path) -> None:
    project_id, profile, root, accounts, conn = _bind_ready(tmp_path, target="draft")
    adapter = _CountingWechat()
    account = AccountConfig(id=profile.id, credentials_path=tmp_path / "x.json")
    result = create_direct(
        conn, project_id=project_id, deliverable_id="dlv_article_wechat_mp",
        actor="lazy", adapter=adapter, account=account,
        publish_config=PublishConfig(enabled=True, allowed_platforms=["wechat_mp"]),
        confirm_token="yes-publish", projects_root=root, accounts_root=accounts,
    )
    assert adapter.calls == 0
    assert result.attempt.mode == "direct"
    assert result.attempt.outcome == "failure"
    assert result.attempt.error and "delivery_target" in result.attempt.error


def test_disabled_publish_config_does_not_call_publisher(tmp_path) -> None:
    project_id, profile, root, accounts, conn = _bind_ready(tmp_path, target="direct")
    adapter = _CountingWechat()
    account = AccountConfig(id=profile.id, credentials_path=tmp_path / "x.json")
    result = create_direct(
        conn, project_id=project_id, deliverable_id="dlv_article_wechat_mp",
        actor="lazy", adapter=adapter, account=account,
        publish_config=PublishConfig(enabled=False, allowed_platforms=["wechat_mp"]),
        confirm_token="yes-publish", projects_root=root, accounts_root=accounts,
    )
    assert adapter.calls == 0
    assert result.attempt.outcome == "failure"
    assert "publish is disabled" in (result.attempt.error or "")


def test_unknown_direct_with_retry_of_id_does_not_call_publisher(tmp_path) -> None:
    project_id, profile, root, accounts, conn = _bind_ready(tmp_path, target="direct")
    adapter = _CountingWechat(unknown=True)
    account = AccountConfig(id=profile.id, credentials_path=tmp_path / "x.json")
    cfg = PublishConfig(enabled=True, allowed_platforms=["wechat_mp"])
    first = create_direct(
        conn, project_id=project_id, deliverable_id="dlv_article_wechat_mp",
        actor="lazy", adapter=adapter, account=account, publish_config=cfg,
        confirm_token="yes-publish", projects_root=root, accounts_root=accounts,
    )
    assert first.attempt.outcome == "unknown"
    assert adapter.calls == 1
    second = create_direct(
        conn, project_id=project_id, deliverable_id="dlv_article_wechat_mp",
        actor="lazy", adapter=adapter, account=account, publish_config=cfg,
        confirm_token="yes-publish", projects_root=root, accounts_root=accounts,
        retry_of_id=first.attempt.id,
    )
    assert adapter.calls == 1
    assert second.replayed is True
    assert second.attempt.outcome == "unknown"


def test_unknown_direct_receipt_is_not_retried(tmp_path) -> None:
    project_id, profile, root, accounts, conn = _bind_ready(tmp_path, target="direct")
    adapter = _CountingWechat(unknown=True)
    account = AccountConfig(id=profile.id, credentials_path=tmp_path / "x.json")
    cfg = PublishConfig(enabled=True, allowed_platforms=["wechat_mp"])
    first = create_direct(
        conn, project_id=project_id, deliverable_id="dlv_article_wechat_mp",
        actor="lazy", adapter=adapter, account=account, publish_config=cfg,
        confirm_token="yes-publish", projects_root=root, accounts_root=accounts,
    )
    assert first.attempt.outcome == "unknown"
    assert adapter.calls == 1
    second = create_direct(
        conn, project_id=project_id, deliverable_id="dlv_article_wechat_mp",
        actor="lazy", adapter=adapter, account=account, publish_config=cfg,
        confirm_token="yes-publish", projects_root=root, accounts_root=accounts,
    )
    assert second.replayed is True
    assert second.attempt.id == first.attempt.id
    assert adapter.calls == 1


def test_verify_unknown_receipt_queries_without_publishing(tmp_path) -> None:
    project_id, profile, root, accounts, conn = _bind_ready(tmp_path, target="direct")
    adapter = _CountingWechat(unknown=True)
    account = AccountConfig(id=profile.id, credentials_path=tmp_path / "x.json")
    cfg = PublishConfig(enabled=True, allowed_platforms=["wechat_mp"])
    first = create_direct(
        conn, project_id=project_id, deliverable_id="dlv_article_wechat_mp",
        actor="lazy", adapter=adapter, account=account, publish_config=cfg,
        confirm_token="yes-publish", projects_root=root, accounts_root=accounts,
    )
    assert first.attempt.outcome == "unknown"
    assert "publish_id=p_unknown" in (first.attempt.error or "")
    publish_calls = adapter.calls
    adapter.unknown = False
    verified = verify_direct_receipt(
        conn, attempt_id=first.attempt.id, adapter=adapter, actor="lazy",
    )
    assert verified.attempt.outcome == "success"
    assert verified.media_id == "article_ok"
    assert adapter.calls == publish_calls + 1


def test_auto_direct_skips_per_article_approval(tmp_path) -> None:
    root = tmp_path / "projects"
    accounts = tmp_path / "accounts"
    project_id = _ready(root, project_id="prj_autoappr")
    creds = _creds(tmp_path / "wechat_mp_main.json", "wxMAIN")
    profile = _profile(
        accounts, profile_id="acc_autoappr", config_id="main",
        creds=str(Path("secrets") / creds.name), target="direct",
    )
    bind_project_account(
        project_id, platform="wechat_mp", account_id=profile.id, now=NOW,
        projects_root=root, accounts_root=accounts,
    )
    save_authorization(
        profile.id, actor="lazy", now=NOW, accounts_root=accounts,
        operations_enabled=True, allow_draft=True, allow_direct=True, quality_floor=6.0,
    )
    from pipeline.account_authorization import load_authorization, quality_fingerprint
    from pipeline.master_documents import load_master
    auth = load_authorization(profile.id, accounts_root=accounts)
    master = load_master(project_id, projects_root=root)
    record_quality_result(
        profile.id, project_id=project_id, score=8.0, verdict="pass",
        now=NOW, accounts_root=accounts,
        content_fingerprint=quality_fingerprint(project_id, projects_root=root),
        master_version=0 if master is None else master.version,
        authorization_version=auth.version,
    )
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    adapter = _CountingWechat()
    account = AccountConfig(id=profile.id, credentials_path=tmp_path / "x.json")
    result = create_direct(
        conn, project_id=project_id, deliverable_id="dlv_article_wechat_mp",
        actor="ops", adapter=adapter, account=account,
        publish_config=PublishConfig(enabled=True, allowed_platforms=["wechat_mp"]),
        confirm_token="yes-publish", projects_root=root, accounts_root=accounts,
        path="auto",
    )
    assert result.attempt.outcome == "success"
    assert adapter.calls == 1


def test_authorized_direct_uses_freepublish_mode(tmp_path) -> None:
    project_id, profile, root, accounts, conn = _bind_ready(tmp_path, target="direct")
    adapter = _CountingWechat()
    account = AccountConfig(id=profile.id, credentials_path=tmp_path / "x.json")
    result = create_direct(
        conn, project_id=project_id, deliverable_id="dlv_article_wechat_mp",
        actor="lazy", adapter=adapter, account=account,
        publish_config=PublishConfig(enabled=True, allowed_platforms=["wechat_mp"]),
        confirm_token="yes-publish", projects_root=root, accounts_root=accounts,
    )
    assert adapter.calls == 1
    assert adapter.modes == ["direct"]
    assert result.attempt.outcome == "success"
    assert result.media_id == "article_ok"


def test_wechat_direct_freepublish_unknown_status(tmp_path: Path) -> None:
    content_dir = tmp_path / "content"
    wechat_dir = content_dir / "wechat_mp"
    wechat_dir.mkdir(parents=True)
    (wechat_dir / "meta.json").write_text(
        json.dumps({"title": "标题", "digest": "摘要"}, ensure_ascii=False), encoding="utf-8",
    )
    (wechat_dir / "article.md").write_text("# 标题\n\n正文", encoding="utf-8")
    (content_dir / "cover.png").write_bytes(b"\x89PNG\r\n" + b"x" * 80)
    calls: list[str] = []

    def fake_get(url, *, params, timeout=30.0):
        return {"access_token": "tok", "expires_in": 7200}

    def fake_upload(url, *, params, file_path, timeout=60.0):
        if url.endswith("/media/uploadimg"):
            return {"url": "https://mmbiz.qpic.cn/x"}
        return {"media_id": "cover1"}

    def fake_post(url, *, params, json_body, timeout=30.0):
        calls.append(url)
        if url.endswith("/draft/add"):
            return {"media_id": "draft1"}
        if url.endswith("/freepublish/submit"):
            return {"publish_id": "p1"}
        if url.endswith("/freepublish/get"):
            return {"publish_id": "p1", "publish_status": 1}
        raise AssertionError(url)

    adapter = WechatMpPublisher(
        app_id="wx1", app_secret="s1",
        http_get=fake_get, http_post=fake_post, http_upload=fake_upload,
    )
    bundle = PostBundle("c1", "t", content_dir / "canonical.md", (), (), {"delivery_mode": "direct"})
    with pytest.raises(Exception, match="unknown receipt"):
        adapter.publish(bundle, AccountConfig("main", tmp_path / "x.json"), dry_run=False)
    assert any(url.endswith("/freepublish/submit") for url in calls)
    assert any(url.endswith("/freepublish/get") for url in calls)


def test_wechat_draft_still_skips_freepublish(tmp_path: Path) -> None:
    content_dir = tmp_path / "content"
    wechat_dir = content_dir / "wechat_mp"
    wechat_dir.mkdir(parents=True)
    (wechat_dir / "meta.json").write_text(
        json.dumps({"title": "标题", "digest": "摘要"}, ensure_ascii=False), encoding="utf-8",
    )
    (wechat_dir / "article.md").write_text("# 标题\n\n正文", encoding="utf-8")
    (content_dir / "cover.png").write_bytes(b"\x89PNG\r\n" + b"x" * 80)
    calls: list[str] = []

    def fake_get(url, *, params, timeout=30.0):
        return {"access_token": "tok", "expires_in": 7200}

    def fake_upload(url, *, params, file_path, timeout=60.0):
        if url.endswith("/media/uploadimg"):
            return {"url": "https://mmbiz.qpic.cn/x"}
        return {"media_id": "cover1"}

    def fake_post(url, *, params, json_body, timeout=30.0):
        calls.append(url)
        return {"media_id": "draft1"}

    adapter = WechatMpPublisher(
        app_id="wx1", app_secret="s1",
        http_get=fake_get, http_post=fake_post, http_upload=fake_upload,
    )
    bundle = PostBundle("c1", "t", content_dir / "canonical.md", (), (), {})
    result = adapter.publish(bundle, AccountConfig("main", tmp_path / "x.json"), dry_run=False)
    assert result.platform_post_id == "draft1"
    assert all("/freepublish/" not in url for url in calls)


def test_stale_draft_media_is_not_used_for_new_approval(tmp_path) -> None:
    from pipeline import approvals, variants
    project_id, profile, root, accounts, conn = _bind_ready(tmp_path, target="direct")
    adapter = _CountingWechat()
    account = AccountConfig(id=profile.id, credentials_path=tmp_path / "x.json")
    cfg = PublishConfig(enabled=True, allowed_platforms=["wechat_mp"])
    drafted = create_draft(
        conn, project_id=project_id, deliverable_id="dlv_article_wechat_mp",
        actor="lazy", adapter=adapter, account=account, publish_config=cfg,
        projects_root=root,
    )
    assert drafted.attempt.deliverable_version == 1
    current = next(
        item for item in variants.load_variants(project_id, projects_root=root).variants
        if item.platform == "wechat_mp"
    )
    variants.set_locked(
        project_id, "wechat_mp", locked=False, now="2026-09-10T12:59:00+00:00",
        projects_root=root,
    )
    variants.save_manual(
        project_id, "wechat_mp", title="新版标题", summary="新版摘要足够长。",
        body="这是重新审批后的第二版正文。" * 50,
        asset_ids=list(current.asset_ids), now="2026-09-10T13:00:00+00:00",
        projects_root=root,
    )
    variants.set_locked(
        project_id, "wechat_mp", locked=True, now="2026-09-10T13:00:30+00:00",
        projects_root=root,
    )
    approvals.recheck(project_id, actor="lazy", now="2026-09-10T13:01:00+00:00", projects_root=root)
    for check in ("master", "visuals", "wechat_mp", "toutiao"):
        approvals.decide(
            project_id, check, approved=True, note=None, actor="lazy",
            now="2026-09-10T13:02:00+00:00", projects_root=root,
        )
    adapter.calls = 0
    with pytest.raises(DeliveryError) as err:
        create_direct(
            conn, project_id=project_id, deliverable_id="dlv_article_wechat_mp",
            actor="lazy", adapter=adapter, account=account, publish_config=cfg,
            confirm_token="yes-publish", projects_root=root, accounts_root=accounts,
        )
    assert adapter.calls == 0
    assert err.value.code == "draft_stale"


def test_concurrent_draft_to_direct_only_publishes_once(tmp_path) -> None:
    import threading
    import time
    project_id, profile, root, accounts, conn = _bind_ready(tmp_path, target="direct")
    adapter = _CountingWechat()
    original = adapter.publish

    def slow_publish(bundle, account, dry_run=False):
        time.sleep(0.15)
        return original(bundle, account, dry_run=dry_run)

    adapter.publish = slow_publish  # type: ignore[method-assign]
    account = AccountConfig(id=profile.id, credentials_path=tmp_path / "x.json")
    cfg = PublishConfig(enabled=True, allowed_platforms=["wechat_mp"])
    create_draft(
        conn, project_id=project_id, deliverable_id="dlv_article_wechat_mp",
        actor="lazy", adapter=adapter, account=account, publish_config=cfg,
        projects_root=root,
    )
    adapter.calls = 0
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)
    db_path = tmp_path / "state.db"

    def worker() -> None:
        local = db.connect(db_path)
        try:
            barrier.wait(timeout=5)
            create_direct(
                local, project_id=project_id, deliverable_id="dlv_article_wechat_mp",
                actor="lazy", adapter=adapter, account=account, publish_config=cfg,
                confirm_token="yes-publish", projects_root=root, accounts_root=accounts,
            )
        except BaseException as exc:
            errors.append(exc)
        finally:
            local.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert adapter.calls == 1
    assert any(getattr(item, "code", "") == "direct_locked" for item in errors) or len(errors) <= 1


def test_verify_unknown_requeries_until_terminal(tmp_path) -> None:
    project_id, profile, root, accounts, conn = _bind_ready(tmp_path, target="direct")
    adapter = _CountingWechat(unknown=True)
    adapter.query_status = 1
    account = AccountConfig(id=profile.id, credentials_path=tmp_path / "x.json")
    cfg = PublishConfig(enabled=True, allowed_platforms=["wechat_mp"])
    first = create_direct(
        conn, project_id=project_id, deliverable_id="dlv_article_wechat_mp",
        actor="lazy", adapter=adapter, account=account, publish_config=cfg,
        confirm_token="yes-publish", projects_root=root, accounts_root=accounts,
    )
    adapter.unknown = False

    def query(publish_id: str) -> dict:
        adapter.calls += 1
        if adapter.query_status == 1:
            return {"publish_id": publish_id, "publish_status": 1}
        return {"publish_id": publish_id, "publish_status": 0, "article_id": "article_ok"}

    adapter.query_freepublish = query
    still = verify_direct_receipt(
        conn, attempt_id=first.attempt.id, adapter=adapter, actor="lazy",
    )
    assert still.attempt.outcome == "unknown"
    queries = adapter.calls
    adapter.query_status = 0
    done = verify_direct_receipt(
        conn, attempt_id=first.attempt.id, adapter=adapter, actor="lazy",
    )
    assert done.attempt.outcome == "success"
    assert adapter.calls > queries


def test_direct_after_draft_does_not_require_queued_publication(tmp_path) -> None:
    project_id, profile, root, accounts, conn = _bind_ready(tmp_path, target="direct")
    adapter = _CountingWechat()
    account = AccountConfig(id=profile.id, credentials_path=tmp_path / "x.json")
    cfg = PublishConfig(enabled=True, allowed_platforms=["wechat_mp"])
    drafted = create_draft(
        conn, project_id=project_id, deliverable_id="dlv_article_wechat_mp",
        actor="lazy", adapter=adapter, account=account, publish_config=cfg,
        projects_root=root,
    )
    assert drafted.attempt.outcome == "success"
    assert drafted.media_id == "media_ok"
    adapter.calls = 0
    adapter.modes = []
    published = create_direct(
        conn, project_id=project_id, deliverable_id="dlv_article_wechat_mp",
        actor="lazy", adapter=adapter, account=account, publish_config=cfg,
        confirm_token="yes-publish", projects_root=root, accounts_root=accounts,
    )
    assert adapter.calls == 1
    assert adapter.modes == ["direct"]
    assert published.attempt.outcome == "success"
    assert published.media_id == "article_ok"


def test_create_draft_still_works_for_bound_account(tmp_path) -> None:
    project_id, profile, root, accounts, conn = _bind_ready(tmp_path, target="draft")
    adapter = _CountingWechat()
    account = AccountConfig(id=profile.id, credentials_path=tmp_path / "x.json")
    result = create_draft(
        conn, project_id=project_id, deliverable_id="dlv_article_wechat_mp",
        actor="lazy", adapter=adapter, account=account,
        publish_config=PublishConfig(enabled=True, allowed_platforms=["wechat_mp"]),
        projects_root=root,
    )
    assert result.attempt.outcome == "success"
    assert result.media_id == "media_ok"
    assert adapter.modes == ["draft"]
