"""M10 Phase D：UI 真实发布端点测试（POST /publications/{id}/publish，无 /preview 后缀）。

完全镜像 tests/webui/test_api_publish_preview.py 的写法，关键断言与 preview 相反：
  - safe_publish 必须以 dry_run=False 被调用
  - adapter.publish 必须被真正调用（preview 绝不调用）
  - 真实 DB 状态会被改变（published_at / platform_post_id / status）
  - 最关键的安全测试：config.publish.enabled=false 时，真实（未 mock）
    safe_publish 拒绝执行——证明该端点不能绕过既有配置门禁。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from pipeline import db
from pipeline.models import (
    Content,
    ContentStatus,
    Publication,
    PublicationStatus,
    Topic,
    TopicStatus,
)
from pipeline.sources.dedup import content_hash
from pipeline.webui import deps


NOW = "2026-07-10T00:00:00+00:00"
PAST = "2026-07-01T00:00:00+00:00"


# ── fixtures（与 test_api_publish_preview.py 一致） ────────────


def _config_yaml(
    tmp_path: Path,
    *,
    publish_enabled: bool = True,
    platforms: dict | None = None,
) -> str:
    creds = tmp_path / "x_credentials.json"
    creds.write_text('{"bearer_token":"test-token"}', encoding="utf-8")

    if platforms is None:
        platforms = {
            "x": {
                "kind": "api",
                "accounts": [{"id": "main", "credentials": str(creds)}],
            }
        }

    platform_lines = ["platforms:"]
    if platforms:
        for name, cfg in platforms.items():
            platform_lines.append(f"  {name}:")
            platform_lines.append(f"    kind: {cfg.get('kind', 'api')}")
            platform_lines.append("    windows: ['08:00-10:00']")
            platform_lines.append("    accounts:")
            for account in cfg.get("accounts", []):
                platform_lines.append(f"      - id: {account['id']}")
                if cfg.get("kind", "api") == "api":
                    credential_path = account.get("credentials", str(creds))
                    platform_lines.append(f"        credentials: {credential_path}")
                else:
                    cookie_path = account.get("cookies", str(tmp_path / "cookies.json"))
                    platform_lines.append(f"        cookies: {cookie_path}")
    else:
        platform_lines.append("  {}")

    enabled = "true" if publish_enabled else "false"
    return (
        "timezone: Asia/Shanghai\n"
        "pillars:\n"
        "  - id: ai_daily\n"
        "    name: AI/科技日报解读\n"
        "    description: d\n"
        "    scoring_hint: s\n"
        "sources: []\n"
        "llm: {tiers: {cheap: m, creative: m, critical: m}}\n"
        "budget: {monthly_usd: 80.0}\n"
        "publish:\n"
        f"  enabled: {enabled}\n"
        "  allowed_platforms: ['x', 'xiaohongshu']\n"
        "  min_gap_hours: 4\n"
        "  max_daily_per_account: 3\n"
        "  cross_platform_gap_minutes: 30\n"
        + "\n".join(platform_lines)
        + "\n"
    )


@pytest.fixture
def tmp_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "state.db"
    conn = db.connect(db_path)
    db.init_db(conn)
    conn.close()

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(_config_yaml(tmp_path), encoding="utf-8")

    monkeypatch.setattr(deps, "_DB_PATH", str(db_path))
    monkeypatch.setattr(deps, "_CONFIG_PATH", str(cfg_path))

    try:
        from pipeline.webui.api import runs
        if hasattr(runs, "_RUNS"):
            runs._RUNS.clear()
    except Exception:
        pass

    return tmp_path


@pytest.fixture
def client(tmp_env: Path) -> TestClient:
    from pipeline.webui.app import create_app
    return TestClient(create_app())


# ── seed helpers（与 test_api_publish_preview.py 一致） ────────


def _seed_topic(conn: sqlite3.Connection, *, topic_id: str, title: str) -> Topic:
    topic = Topic(
        id=topic_id,
        source="rss:test",
        title=title,
        url=None,
        summary=None,
        content_hash=content_hash(title, None),
        pillar="ai_daily",
        score=8.0,
        score_reason="ok",
        status=TopicStatus.CONSUMED.value,
        created_at=NOW,
        updated_at=NOW,
    )
    db.insert_topic(conn, topic)
    return topic


def _seed_content(
    conn: sqlite3.Connection,
    tmp_path: Path,
    *,
    content_id: str = "c_real01",
    title: str = "Real Publish Title",
    with_thread: bool = True,
) -> Content:
    topic_id = "t_" + content_id.removeprefix("c_")
    _seed_topic(conn, topic_id=topic_id, title=f"Topic for {content_id}")

    content_dir = tmp_path / "output" / "2026-07-10" / content_id
    content_dir.mkdir(parents=True, exist_ok=True)
    canonical = content_dir / "canonical.md"
    canonical.write_text("# Real Publish Title\n\n这是用于真实发布测试的正文。" * 20, encoding="utf-8")

    if with_thread:
        x_dir = content_dir / "x"
        x_dir.mkdir(parents=True, exist_ok=True)
        (x_dir / "thread.md").write_text(
            "1/3 第一条推文，足够短。\n\n"
            "2/3 第二条推文，继续说明。\n\n"
            "3/3 第三条推文，收束观点。\n",
            encoding="utf-8",
        )

    content = Content(
        id=content_id,
        topic_id=topic_id,
        pillar="ai_daily",
        title=title,
        canonical_path=str(canonical),
        formats=("x",),
        gate_score_total=27.0,
        gate_scores={"info": 9, "fun": 9, "view": 9},
        gate_verdict="通过",
        status=ContentStatus.APPROVED.value,
        created_at=NOW,
        updated_at=NOW,
    )
    db.insert_content(conn, content)
    return content


def _seed_publication(
    conn: sqlite3.Connection,
    tmp_path: Path,
    *,
    pub_id: str = "p_real01",
    content_id: str = "c_real01",
    platform: str = "x",
    account_id: str = "main",
    status: str = PublicationStatus.QUEUED.value,
    scheduled_at: str = PAST,
    with_thread: bool = True,
) -> Publication:
    _seed_content(conn, tmp_path, content_id=content_id, with_thread=with_thread)
    publication = Publication(
        id=pub_id,
        content_id=content_id,
        platform=platform,
        account_id=account_id,
        scheduled_at=scheduled_at,
        published_at=None,
        platform_post_id=None,
        platform_url=None,
        error=None,
        retry_count=0,
        status=status,
        created_at=NOW,
        updated_at=NOW,
    )
    db.insert_publication(conn, publication)
    return publication


def _get_run(client: TestClient, run_id: str) -> dict:
    response = client.get(f"/api/v1/runs/{run_id}")
    assert response.status_code == 200, response.text
    return response.json()


# ── contract / source checks ─────────────────────────────────


def test_endpoint_source_path_has_no_preview_suffix() -> None:
    from tests.repo_root import REPO_ROOT
    source = (REPO_ROOT / "pipeline/webui/api/publish.py").read_text(
        encoding="utf-8",
    )
    assert '"/publications/{publication_id}/publish"' in source
    assert '"/publications/{publication_id}/publish/preview"' in source  # 两者共存


# ── success path with patched safe_publish / adapter ──────────


def test_post_real_publish_returns_202_and_run_id(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pipeline.webui import publish_bridge

    conn = db.connect(str(tmp_env / "state.db"))
    _seed_publication(conn, tmp_env, pub_id="p_202001")
    conn.close()

    adapter = MagicMock()
    adapter.platform = "x"
    adapter.validate.return_value = []
    adapter.publish.return_value = SimpleNamespace(
        platform_post_id="real-123", url="https://x.example/1",
    )
    monkeypatch.setattr(publish_bridge, "get_adapter", lambda *args, **kwargs: adapter)
    monkeypatch.setattr(
        publish_bridge,
        "safe_publish",
        lambda *args, **kwargs: SimpleNamespace(
            published=True,
            reason="",
            platform_post_id="real-123",
            url="https://x.example/1",
            dry_run=kwargs["dry_run"],
        ),
    )

    response = client.post("/api/v1/publications/p_202001/publish", json={})

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["run_id"].startswith("run_")
    assert body["status"] == "queued"


def test_run_result_has_real_publish_shape(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pipeline.webui import publish_bridge

    conn = db.connect(str(tmp_env / "state.db"))
    _seed_publication(conn, tmp_env, pub_id="p_shape1")
    conn.close()

    adapter = MagicMock()
    adapter.platform = "x"
    adapter.validate.return_value = []
    monkeypatch.setattr(publish_bridge, "get_adapter", lambda *args, **kwargs: adapter)
    monkeypatch.setattr(
        publish_bridge,
        "safe_publish",
        lambda *args, **kwargs: SimpleNamespace(
            published=True,
            reason="",
            platform_post_id="real-shape",
            url="https://x.example/shape",
            dry_run=kwargs["dry_run"],
        ),
    )

    response = client.post("/api/v1/publications/p_shape1/publish", json={})
    run = _get_run(client, response.json()["run_id"])

    assert run["status"] == "succeeded"
    result = run["result"]
    assert set(result) >= {"published", "reason", "platform_post_id", "url"}
    assert result["published"] is True
    assert result["platform_post_id"] == "real-shape"
    assert result["url"] == "https://x.example/shape"


def test_safe_publish_called_with_dry_run_false(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pipeline.webui import publish_bridge

    conn = db.connect(str(tmp_env / "state.db"))
    _seed_publication(conn, tmp_env, pub_id="p_realr1")
    conn.close()

    adapter = MagicMock()
    adapter.platform = "x"
    adapter.validate.return_value = []
    safe_publish = MagicMock(return_value=SimpleNamespace(
        published=True,
        reason="",
        platform_post_id="real-1",
        url="https://x.example/1",
        dry_run=False,
    ))
    monkeypatch.setattr(publish_bridge, "get_adapter", lambda *args, **kwargs: adapter)
    monkeypatch.setattr(publish_bridge, "safe_publish", safe_publish)

    response = client.post("/api/v1/publications/p_realr1/publish", json={})
    run = _get_run(client, response.json()["run_id"])

    assert run["status"] == "succeeded"
    assert safe_publish.call_args.kwargs["dry_run"] is False


def test_real_publish_passes_real_conn_and_real_adapter_not_a_wrapper(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真实发布必须直接把注入的 adapter 原样传给 safe_publish（非包装类）。"""
    from pipeline.webui import publish_bridge

    conn = db.connect(str(tmp_env / "state.db"))
    _seed_publication(conn, tmp_env, pub_id="p_rawad1")
    conn.close()

    adapter = MagicMock()
    adapter.platform = "x"
    adapter.validate.return_value = []
    safe_publish = MagicMock(return_value=SimpleNamespace(
        published=True, reason="", platform_post_id="p1",
        url="https://x.example/p1", dry_run=False,
    ))
    monkeypatch.setattr(publish_bridge, "get_adapter", lambda *args, **kwargs: adapter)
    monkeypatch.setattr(publish_bridge, "safe_publish", safe_publish)

    response = client.post("/api/v1/publications/p_rawad1/publish", json={})
    run = _get_run(client, response.json()["run_id"])

    assert run["status"] == "succeeded"
    called_adapter = safe_publish.call_args.args[2]
    assert called_adapter is adapter


# ── real safe_publish (unmocked) exercised for adapter interaction ──


def test_adapter_publish_is_actually_called_by_real_publish_run(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """与 preview 相反：真实发布必须真正调用 adapter.publish。"""
    from pipeline.webui import publish_bridge

    conn = db.connect(str(tmp_env / "state.db"))
    _seed_publication(conn, tmp_env, pub_id="p_calls1", with_thread=True)
    conn.close()

    adapter = MagicMock()
    adapter.platform = "x"
    adapter.validate.return_value = []
    adapter.publish.return_value = SimpleNamespace(
        platform_post_id="real-called", url="https://x.example/called",
    )
    monkeypatch.setattr(publish_bridge, "get_adapter", lambda *args, **kwargs: adapter)
    # safe_publish 不 mock —— 用真实实现，验证它确实调用了 adapter.publish

    response = client.post("/api/v1/publications/p_calls1/publish", json={})
    run = _get_run(client, response.json()["run_id"])

    assert run["status"] == "succeeded", run
    assert adapter.validate.call_count >= 1
    assert adapter.publish.call_count == 1
    assert adapter.publish.call_args.kwargs["dry_run"] is False
    assert run["result"]["published"] is True
    assert run["result"]["platform_post_id"] == "real-called"


def test_real_publish_updates_real_db_status_to_published(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """与 preview 相反：真实发布必须真的改变 state.db 里的 publication 状态。"""
    from pipeline.webui import publish_bridge

    conn = db.connect(str(tmp_env / "state.db"))
    _seed_publication(conn, tmp_env, pub_id="p_dbchg1", with_thread=True)
    conn.close()

    adapter = MagicMock()
    adapter.platform = "x"
    adapter.validate.return_value = []
    adapter.publish.return_value = SimpleNamespace(
        platform_post_id="real-dbchg", url="https://x.example/dbchg",
    )
    monkeypatch.setattr(publish_bridge, "get_adapter", lambda *args, **kwargs: adapter)

    response = client.post("/api/v1/publications/p_dbchg1/publish", json={})
    run = _get_run(client, response.json()["run_id"])
    assert run["status"] == "succeeded", run

    conn = db.connect(str(tmp_env / "state.db"))
    after = db.get_publication(conn, "p_dbchg1")
    conn.close()
    assert after is not None
    assert after.status == PublicationStatus.PUBLISHED.value
    assert after.platform_post_id == "real-dbchg"
    assert after.platform_url == "https://x.example/dbchg"
    assert after.published_at is not None


# ── critical safety test：publish.enabled=false 不可绕过 ──────


def test_publish_disabled_rejects_with_real_unmocked_safe_publish(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """最关键的安全护栏测试：config.publish.enabled=false 时，真实（未 mock）
    safe_publish 必须拒绝执行——证明 UI 真发端点不能绕过既有配置门禁。"""
    from pipeline.webui import publish_bridge

    cfg_path = tmp_env / "config.yaml"
    cfg_path.write_text(_config_yaml(tmp_env, publish_enabled=False), encoding="utf-8")

    conn = db.connect(str(tmp_env / "state.db"))
    _seed_publication(conn, tmp_env, pub_id="p_disabl", with_thread=True)
    conn.close()

    adapter = MagicMock()
    adapter.platform = "x"
    adapter.validate.return_value = []
    adapter.publish.return_value = SimpleNamespace(
        platform_post_id="should-not-happen", url="https://x.example/never",
    )
    monkeypatch.setattr(publish_bridge, "get_adapter", lambda *args, **kwargs: adapter)
    # safe_publish 不 mock —— 用真实实现验证 config.enabled 门禁生效

    response = client.post("/api/v1/publications/p_disabl/publish", json={})
    run = _get_run(client, response.json()["run_id"])

    assert run["status"] == "succeeded", run
    result = run["result"]
    assert result["published"] is False
    assert result["reason"] == "publish is disabled"
    assert adapter.publish.call_count == 0

    conn = db.connect(str(tmp_env / "state.db"))
    after = db.get_publication(conn, "p_disabl")
    conn.close()
    assert after is not None
    assert after.status == PublicationStatus.QUEUED.value
    assert after.published_at is None


def test_platform_not_in_allowed_platforms_rejects_with_real_safe_publish(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """allowed_platforms 白名单同样不可被 UI 真发端点绕过。"""
    from pipeline.webui import publish_bridge

    cookies_path = tmp_env / "douyin_cookies.json"
    cookies_path.write_text('{"cookies": [], "origins": []}', encoding="utf-8")
    cfg_path = tmp_env / "config.yaml"
    cfg_path.write_text(
        _config_yaml(
            tmp_env,
            platforms={
                "x": {
                    "kind": "api",
                    "accounts": [{"id": "main", "credentials": str(tmp_env / "x_credentials.json")}],
                },
                "douyin": {
                    "kind": "playwright",
                    "accounts": [{"id": "main", "cookies": str(cookies_path)}],
                },
            },
        ),
        encoding="utf-8",
    )

    conn = db.connect(str(tmp_env / "state.db"))
    _seed_publication(conn, tmp_env, pub_id="p_notallw", platform="douyin", with_thread=True)
    conn.close()

    adapter = MagicMock()
    adapter.platform = "douyin"
    adapter.validate.return_value = []
    monkeypatch.setattr(publish_bridge, "get_adapter", lambda *args, **kwargs: adapter)

    response = client.post("/api/v1/publications/p_notallw/publish", json={})
    run = _get_run(client, response.json()["run_id"])

    assert run["status"] == "succeeded", run
    result = run["result"]
    assert result["published"] is False
    assert "allowed_platforms" in result["reason"]
    assert adapter.publish.call_count == 0


# ── failed run mappings（镜像 preview 的域错误分类） ───────────


@pytest.mark.parametrize(
    ("case_name", "seed_kwargs", "config_platforms", "expected_code"),
    [
        (
            "publication_not_found",
            None,
            None,
            "publication_not_found",
        ),
        (
            "wrong_status",
            {"pub_id": "p_fail01", "status": PublicationStatus.PUBLISHED.value},
            None,
            "wrong_status",
        ),
        (
            "platform_not_configured",
            {"pub_id": "p_fail02", "platform": "x"},
            {},
            "platform_not_configured",
        ),
        (
            "account_not_found",
            {"pub_id": "p_fail03", "account_id": "missing"},
            {
                "x": {
                    "kind": "api",
                    "accounts": [{"id": "main"}],
                }
            },
            "account_not_found",
        ),
    ],
)
def test_real_publish_domain_errors_become_failed_runs(
    client: TestClient,
    tmp_env: Path,
    case_name: str,
    seed_kwargs: dict | None,
    config_platforms: dict | None,
    expected_code: str,
) -> None:
    cfg_path = tmp_env / "config.yaml"
    if config_platforms is not None:
        cfg_path.write_text(
            _config_yaml(tmp_env, platforms=config_platforms),
            encoding="utf-8",
        )

    if seed_kwargs is not None:
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_publication(conn, tmp_env, **seed_kwargs)
        conn.close()
        pub_id = seed_kwargs["pub_id"]
    else:
        pub_id = "p_missing"

    response = client.post(f"/api/v1/publications/{pub_id}/publish", json={})
    assert response.status_code == 202, response.text
    run = _get_run(client, response.json()["run_id"])

    assert run["status"] == "failed", case_name
    assert run["error_code"] == expected_code
    assert run["error"]


def test_real_publish_adapter_init_error_becomes_failed_run(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pipeline.webui import publish_bridge

    conn = db.connect(str(tmp_env / "state.db"))
    _seed_publication(conn, tmp_env, pub_id="p_adapt1")
    conn.close()

    def boom(*args, **kwargs):
        raise FileNotFoundError("credentials missing")

    monkeypatch.setattr(publish_bridge, "get_adapter", boom)

    response = client.post("/api/v1/publications/p_adapt1/publish", json={})
    run = _get_run(client, response.json()["run_id"])

    assert run["status"] == "failed"
    assert run["error_code"] == "adapter_init_error"
    assert "credentials missing" in run["error"]
