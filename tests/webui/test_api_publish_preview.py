"""M10-12 阶段 E：UI dry-run 发布预演端点测试。

覆盖重点：
  - POST /api/v1/publications/{id}/publish/preview 路径必须含 /preview
  - 202 + run_id；GET /api/v1/runs/{run_id} 取后台结果
  - 后台只调用 safe_publish(dry_run=True)，绝不触达原始 adapter.publish
  - 真实 adapter.validate 路径可跑（XApiPublisher 本地校验，不触网络）
  - dry-run 预演不改变真实 DB publication 状态 / updated_at
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
FUTURE = "2030-01-01T00:00:00+00:00"
PAST = "2026-07-01T00:00:00+00:00"


# ── fixtures ────────────────────────────────────────────────


def _config_yaml(
    tmp_path: Path,
    *,
    publish_enabled: bool = True,
    platforms: dict | None = None,
) -> str:
    """生成测试 config.yaml。platforms=None 表示默认配置 x/main。"""
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
        platform_lines.append("  {}");

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

    # 每个用例清掉内存 run registry，避免跨测试污染。
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


# ── seed helpers ─────────────────────────────────────────────


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
    content_id: str = "c_prev01",
    title: str = "Preview Title",
    with_thread: bool = True,
    with_xhs: bool = False,
) -> Content:
    topic_id = "t_" + content_id.removeprefix("c_")
    _seed_topic(conn, topic_id=topic_id, title=f"Topic for {content_id}")

    content_dir = tmp_path / "output" / "2026-07-10" / content_id
    content_dir.mkdir(parents=True, exist_ok=True)
    canonical = content_dir / "canonical.md"
    canonical.write_text("# Preview Title\n\n这是用于 dry-run 预演的正文。" * 20, encoding="utf-8")

    formats: tuple[str, ...] = ("x",)
    if with_thread:
        x_dir = content_dir / "x"
        x_dir.mkdir(parents=True, exist_ok=True)
        (x_dir / "thread.md").write_text(
            "1/3 第一条推文，足够短。\n\n"
            "2/3 第二条推文，继续说明。\n\n"
            "3/3 第三条推文，收束观点。\n",
            encoding="utf-8",
        )
    if with_xhs:
        formats = ("xiaohongshu",)
        xhs_dir = content_dir / "xiaohongshu"
        xhs_dir.mkdir(parents=True, exist_ok=True)
        (xhs_dir / "slides.json").write_text(
            '[{"title":"卡1","body":"内容1"},{"title":"卡2","body":"内容2"},{"title":"卡3","body":"内容3"}]',
            encoding="utf-8",
        )
        (xhs_dir / "caption.md").write_text("小红书正文" * 30, encoding="utf-8")
        (xhs_dir / "tags.txt").write_text("#AI\n#自动化\n", encoding="utf-8")
        (xhs_dir / "cover.png").write_bytes(b"png")
        (xhs_dir / "card-01.png").write_bytes(b"png")
        (xhs_dir / "card-02.png").write_bytes(b"png")

    content = Content(
        id=content_id,
        topic_id=topic_id,
        pillar="ai_daily",
        title=title,
        canonical_path=str(canonical),
        formats=formats,
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
    pub_id: str = "p_prev01",
    content_id: str = "c_prev01",
    platform: str = "x",
    account_id: str = "main",
    status: str = PublicationStatus.QUEUED.value,
    scheduled_at: str = PAST,
    with_thread: bool = True,
    with_xhs: bool = False,
) -> Publication:
    _seed_content(
        conn,
        tmp_path,
        content_id=content_id,
        with_thread=with_thread,
        with_xhs=with_xhs,
    )
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


def test_endpoint_source_path_contains_preview() -> None:
    source = Path("/Users/lazy/Code/crack/MediaForge/pipeline/webui/api/publish.py").read_text(
        encoding="utf-8",
    )
    assert '/publications/{publication_id}/publish/preview' in source


# ── success path with patched safe_publish / adapter ──────────


def test_post_preview_returns_202_and_run_id(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pipeline.webui import preview_bridge

    conn = db.connect(str(tmp_env / "state.db"))
    _seed_publication(conn, tmp_env, pub_id="p_202001")
    conn.close()

    adapter = MagicMock()
    adapter.platform = "x"
    adapter.validate.return_value = []
    adapter.publish = MagicMock()
    monkeypatch.setattr(preview_bridge, "get_adapter", lambda *args, **kwargs: adapter)
    monkeypatch.setattr(
        preview_bridge,
        "safe_publish",
        lambda *args, **kwargs: SimpleNamespace(
            published=False,
            reason="preview ok",
            dry_run=kwargs["dry_run"],
        ),
    )

    response = client.post("/api/v1/publications/p_202001/publish/preview", json={})

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["run_id"].startswith("run_")
    assert body["status"] == "queued"


def test_run_result_has_preview_shape_and_dry_run_true(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pipeline.webui import preview_bridge

    conn = db.connect(str(tmp_env / "state.db"))
    _seed_publication(conn, tmp_env, pub_id="p_shape1")
    conn.close()

    adapter = MagicMock()
    adapter.platform = "x"
    adapter.validate.return_value = []
    adapter.publish = MagicMock()
    monkeypatch.setattr(preview_bridge, "get_adapter", lambda *args, **kwargs: adapter)
    monkeypatch.setattr(
        preview_bridge,
        "safe_publish",
        lambda *args, **kwargs: SimpleNamespace(
            published=False,
            reason="preview ok",
            dry_run=kwargs["dry_run"],
        ),
    )

    response = client.post("/api/v1/publications/p_shape1/publish/preview", json={})
    run = _get_run(client, response.json()["run_id"])

    assert run["status"] == "succeeded"
    result = run["result"]
    assert set(result) >= {
        "validate_passed",
        "validate_errors",
        "preview",
        "safe_publish_result",
    }
    assert result["validate_passed"] is True
    assert result["validate_errors"] == []
    assert result["preview"]["title"] == "Preview Title"
    assert result["preview"]["platform"] == "x"
    assert result["preview"]["account_id"] == "main"
    assert result["preview"]["scheduled_at"] == PAST
    assert result["safe_publish_result"]["published"] is False
    assert result["safe_publish_result"]["reason"] == "preview ok"
    assert result["safe_publish_result"]["dry_run"] is True


def test_safe_publish_called_with_dry_run_true(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pipeline.webui import preview_bridge

    conn = db.connect(str(tmp_env / "state.db"))
    _seed_publication(conn, tmp_env, pub_id="p_dry001")
    conn.close()

    adapter = MagicMock()
    adapter.platform = "x"
    adapter.validate.return_value = []
    adapter.publish = MagicMock()
    safe_publish = MagicMock(return_value=SimpleNamespace(
        published=False,
        reason="preview ok",
        dry_run=True,
    ))
    monkeypatch.setattr(preview_bridge, "get_adapter", lambda *args, **kwargs: adapter)
    monkeypatch.setattr(preview_bridge, "safe_publish", safe_publish)

    response = client.post("/api/v1/publications/p_dry001/publish/preview", json={})
    run = _get_run(client, response.json()["run_id"])

    assert run["status"] == "succeeded"
    assert safe_publish.call_args.kwargs["dry_run"] is True


def test_adapter_publish_never_called_by_preview_run(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pipeline.webui import preview_bridge

    conn = db.connect(str(tmp_env / "state.db"))
    _seed_publication(conn, tmp_env, pub_id="p_nopub1")
    conn.close()

    adapter = MagicMock()
    adapter.platform = "x"
    adapter.validate.return_value = []
    adapter.publish = MagicMock()
    monkeypatch.setattr(preview_bridge, "get_adapter", lambda *args, **kwargs: adapter)
    monkeypatch.setattr(
        preview_bridge,
        "safe_publish",
        lambda *args, **kwargs: SimpleNamespace(
            published=False,
            reason="preview ok",
            dry_run=True,
        ),
    )

    response = client.post("/api/v1/publications/p_nopub1/publish/preview", json={})
    run = _get_run(client, response.json()["run_id"])

    assert run["status"] == "succeeded"
    assert adapter.validate.call_count >= 1
    assert adapter.publish.call_count == 0


def test_preview_run_does_not_change_publication_status_or_updated_at(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pipeline.webui import preview_bridge

    conn = db.connect(str(tmp_env / "state.db"))
    before = _seed_publication(conn, tmp_env, pub_id="p_dbfree")
    conn.close()

    adapter = MagicMock()
    adapter.platform = "x"
    adapter.validate.return_value = []
    adapter.publish = MagicMock()
    monkeypatch.setattr(preview_bridge, "get_adapter", lambda *args, **kwargs: adapter)
    monkeypatch.setattr(
        preview_bridge,
        "safe_publish",
        lambda *args, **kwargs: SimpleNamespace(
            published=True,
            reason="",
            dry_run=True,
        ),
    )

    response = client.post("/api/v1/publications/p_dbfree/publish/preview", json={})
    run = _get_run(client, response.json()["run_id"])
    assert run["status"] == "succeeded"

    conn = db.connect(str(tmp_env / "state.db"))
    after = db.get_publication(conn, "p_dbfree")
    conn.close()
    assert after is not None
    assert after.status == PublicationStatus.QUEUED.value
    assert after.updated_at == before.updated_at
    assert after.published_at is None
    assert after.platform_post_id is None
    assert after.platform_url is None


# ── actual safe_publish disabled path ────────────────────────


def test_publish_disabled_succeeds_with_disabled_reason_and_dry_run_true(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pipeline.webui import preview_bridge

    cfg_path = tmp_env / "config.yaml"
    cfg_path.write_text(_config_yaml(tmp_env, publish_enabled=False), encoding="utf-8")

    conn = db.connect(str(tmp_env / "state.db"))
    _seed_publication(conn, tmp_env, pub_id="p_disabl")
    conn.close()

    adapter = MagicMock()
    adapter.platform = "x"
    adapter.validate.return_value = []
    adapter.publish = MagicMock()
    monkeypatch.setattr(preview_bridge, "get_adapter", lambda *args, **kwargs: adapter)

    response = client.post("/api/v1/publications/p_disabl/publish/preview", json={})
    run = _get_run(client, response.json()["run_id"])

    assert run["status"] == "succeeded"
    safe_result = run["result"]["safe_publish_result"]
    assert safe_result["published"] is False
    assert safe_result["reason"] == "publish is disabled"
    assert safe_result["dry_run"] is True
    assert adapter.publish.call_count == 0


# ── failed run mappings ──────────────────────────────────────


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
def test_preview_domain_errors_become_failed_runs(
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

    response = client.post(f"/api/v1/publications/{pub_id}/publish/preview", json={})
    assert response.status_code == 202, response.text
    run = _get_run(client, response.json()["run_id"])

    assert run["status"] == "failed", case_name
    assert run["error_code"] == expected_code
    assert run["error"]


def test_adapter_init_error_becomes_failed_run(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pipeline.webui import preview_bridge

    conn = db.connect(str(tmp_env / "state.db"))
    _seed_publication(conn, tmp_env, pub_id="p_adapt1")
    conn.close()

    def boom(*args, **kwargs):
        raise FileNotFoundError("credentials missing")

    monkeypatch.setattr(preview_bridge, "get_adapter", boom)

    response = client.post("/api/v1/publications/p_adapt1/publish/preview", json={})
    run = _get_run(client, response.json()["run_id"])

    assert run["status"] == "failed"
    assert run["error_code"] == "adapter_init_error"
    assert "credentials missing" in run["error"]


# ── real validate path / preview bundle ──────────────────────


def test_real_x_adapter_validate_path_runs_without_mocking_validate(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真实 XApiPublisher.validate 读取 x/thread.md；只 mock safe_publish 副作用。"""
    from pipeline.webui import preview_bridge

    conn = db.connect(str(tmp_env / "state.db"))
    _seed_publication(conn, tmp_env, pub_id="p_realv1", with_thread=True)
    conn.close()

    safe_publish = MagicMock(return_value=SimpleNamespace(
        published=False,
        reason="preview ok",
        dry_run=True,
    ))
    monkeypatch.setattr(preview_bridge, "safe_publish", safe_publish)

    response = client.post("/api/v1/publications/p_realv1/publish/preview", json={})
    run = _get_run(client, response.json()["run_id"])

    assert run["status"] == "succeeded"
    assert run["result"]["validate_passed"] is True
    assert run["result"]["validate_errors"] == []
    assert safe_publish.call_args.kwargs["dry_run"] is True


def test_real_x_adapter_validate_errors_are_returned(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真实 XApiPublisher.validate 返回缺失 thread.md 的问题列表。"""
    from pipeline.webui import preview_bridge

    conn = db.connect(str(tmp_env / "state.db"))
    _seed_publication(
        conn,
        tmp_env,
        pub_id="p_valerr",
        content_id="c_valerr",
        with_thread=False,
    )
    conn.close()

    monkeypatch.setattr(
        preview_bridge,
        "safe_publish",
        lambda *args, **kwargs: SimpleNamespace(
            published=False,
            reason="validate: missing thread",
            dry_run=True,
        ),
    )

    response = client.post("/api/v1/publications/p_valerr/publish/preview", json={})
    run = _get_run(client, response.json()["run_id"])

    assert run["status"] == "succeeded"
    result = run["result"]
    assert result["validate_passed"] is False
    assert result["validate_errors"]
    assert any("thread" in err for err in result["validate_errors"])


def test_build_preview_bundle_includes_xhs_tags_and_media(tmp_env: Path) -> None:
    from pipeline.webui.preview_bridge import _build_preview_bundle

    conn = db.connect(str(tmp_env / "state.db"))
    pub = _seed_publication(
        conn,
        tmp_env,
        pub_id="p_xhs001",
        content_id="c_xhs001",
        platform="xiaohongshu",
        account_id="main",
        with_xhs=True,
    )

    bundle = _build_preview_bundle(conn, pub)
    conn.close()

    assert bundle.title == "Preview Title"
    assert bundle.body_path.name == "canonical.md"
    assert [p.name for p in bundle.media_paths] == [
        "cover.png",
        "card-01.png",
        "card-02.png",
    ]
    assert bundle.tags == ("AI", "自动化")


def test_get_unknown_run_returns_404(client: TestClient) -> None:
    response = client.get("/api/v1/runs/run_missing")
    assert response.status_code == 404
    body = response.json()
    assert body["detail"]["error"]["code"] == "run_not_found"
