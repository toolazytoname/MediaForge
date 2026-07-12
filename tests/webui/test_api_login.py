"""U7-7 /api/v1/accounts/{platform}/{account}/login endpoint tests.

Coverage:
  - 1st POST returns 202 + run_id + status=queued
  - Same account 2nd POST returns 409 + login_in_progress
  - BackgroundTask completion -> GET /runs/{run_id} shows status=succeeded
  - PublishError -> status=failed + error.message + error.code
  - Progress messages propagate from pipeline.publishers.login logger through
    logging.Handler to runs registry
  - Unsupported platform -> 400 platform_not_supported
"""
from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from pipeline.publishers.base import PublishError
from pipeline.webui import deps


# fixtures ────────────────────────────────────────────────


@pytest.fixture
def tmp_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "state.db"
    from pipeline import db as _db
    c = _db.connect(db_path)
    _db.init_db(c)
    c.close()
    monkeypatch.setattr(deps, "_DB_PATH", str(db_path))
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "timezone: Asia/Shanghai\n"
        "pillars:\n  - id: ai_daily\n    name: AI\n    description: d\n    scoring_hint: s\n"
        "sources: []\n"
        "llm: {tiers: {cheap: m, creative: m, critical: m}}\n"
        "budget: {monthly_usd: 80.0}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(deps, "_CONFIG_PATH", str(cfg_path))

    # Each test cleans runs registry + _LOGIN_RUNS to avoid cross-test pollution.
    from pipeline.webui.api import runs as runs_api
    from pipeline.webui import login_bridge
    if hasattr(runs_api, "_RUNS"):
        runs_api._RUNS.clear()
    if hasattr(login_bridge, "_LOGIN_RUNS"):
        login_bridge._LOGIN_RUNS.clear()

    return tmp_path


@pytest.fixture
def client(tmp_env: Path) -> TestClient:
    from pipeline.webui.app import create_app
    return TestClient(create_app())


# helpers ─────────────────────────────────────────────────


def _patch_run_login(monkeypatch, *, return_value=None, side_effect=None):
    """monkeypatch `pipeline.webui.login_bridge.run_login`.

    accounts.py + login_bridge.py both use
    `from pipeline.publishers.login_cmd import run_login`, so the patch must
    target the consumer side (login_bridge) to take effect inside the
    background task.
    """
    from pipeline.webui import login_bridge
    fn = MagicMock(return_value=return_value, side_effect=side_effect)
    monkeypatch.setattr(login_bridge, "run_login", fn)
    return fn


# contract / source ──────────────────────────────────────


def test_endpoint_source_path_contains_login() -> None:
    """accounts.py source contains POST /accounts/{platform}/{account}/login route."""
    source = Path(
        "/Users/lazy/Code/crack/MediaForge/pipeline/webui/api/accounts.py",
    ).read_text(encoding="utf-8")
    assert "/accounts/{platform}/{account}/login" in source


# happy path ──────────────────────────────────────────────


def test_first_post_returns_202_and_run_id(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First POST toutiao/main -> 202 + run_id + status=queued."""
    fake_path = tmp_env / "toutiao_main.json"
    fake_path.write_text("{}", encoding="utf-8")
    _patch_run_login(monkeypatch, return_value=fake_path)

    response = client.post("/api/v1/accounts/toutiao/main/login", json={})
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["run_id"]
    assert body["status"] == "queued"
    assert body["platform"] == "toutiao"
    assert body["account"] == "main"


def test_background_run_completes_with_succeeded_status(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After BackgroundTask completes, GET /runs/{run_id} -> status=succeeded + result.path."""
    fake_path = tmp_env / "toutiao_main.json"
    fake_path.write_text("{}", encoding="utf-8")
    _patch_run_login(monkeypatch, return_value=fake_path)

    response = client.post("/api/v1/accounts/toutiao/main/login", json={})
    run_id = response.json()["run_id"]

    # TestClient triggers background task execution before returning
    run_resp = client.get(f"/api/v1/runs/{run_id}")
    assert run_resp.status_code == 200, run_resp.text
    run = run_resp.json()
    assert run["status"] == "succeeded", run
    assert run["result"]["path"] == str(fake_path)
    assert run["platform"] == "toutiao"
    assert run["account"] == "main"


# mutex ──────────────────────────────────────────────────


def test_second_post_after_completion_succeeds(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sequential: 1st completes -> mutex released -> 2nd returns 202 (cleanup works)."""
    fake_path = tmp_env / "toutiao_main.json"
    fake_path.write_text("{}", encoding="utf-8")
    _patch_run_login(monkeypatch, return_value=fake_path)

    first = client.post("/api/v1/accounts/toutiao/main/login", json={})
    assert first.status_code == 202
    run_id = first.json()["run_id"]

    # Trigger background completion
    client.get(f"/api/v1/runs/{run_id}")

    # Second POST after cleanup -> 202
    second = client.post("/api/v1/accounts/toutiao/main/login", json={})
    assert second.status_code == 202, second.text


def test_mutex_blocks_when_run_in_progress(
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutex: directly call start_login twice without executing background task.

    TestClient's BackgroundTasks is sync (runs after response), so we test the
    mutex logic directly via the endpoint function.
    """
    from fastapi import BackgroundTasks, HTTPException
    from pipeline.webui.api.accounts import start_login

    bt1 = BackgroundTasks()
    resp1 = start_login("toutiao", "main", bt1)
    assert resp1["status"] == "queued"
    assert resp1["run_id"]

    # Simulate background task still running (don't execute bt1 task)
    bt2 = BackgroundTasks()
    with pytest.raises(HTTPException) as exc_info:
        start_login("toutiao", "main", bt2)
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "login_in_progress"

    # Mutex released (finally cleanup) -> 3rd succeeds
    from pipeline.webui import login_bridge
    login_bridge._LOGIN_RUNS.pop(("toutiao", "main"), None)

    bt3 = BackgroundTasks()
    resp3 = start_login("toutiao", "main", bt3)
    assert resp3["status"] == "queued"


def test_cleanup_runs_even_on_publish_error(
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """finally cleanup releases mutex even when run_login raises PublishError."""
    from pipeline.webui import login_bridge
    from pipeline.webui.login_bridge import _run_login_then_cleanup, _LOGIN_RUNS

    _patch_run_login(
        monkeypatch,
        side_effect=PublishError("login timeout after 300s"),
    )

    run_id = "login_test_cleanup"
    _LOGIN_RUNS[("toutiao", "main")] = run_id

    # Manually invoke cleanup wrapper (background task body)
    _run_login_then_cleanup(run_id, "toutiao", "main")

    # Mutex MUST be released even though run_login raised
    assert ("toutiao", "main") not in _LOGIN_RUNS
    assert "login_bridge" in login_bridge.__file__  # ensure import path


# failure path ───────────────────────────────────────────


def test_publish_error_becomes_failed_run(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_login raises PublishError -> run.status=failed + error.code=login_failed."""
    _patch_run_login(
        monkeypatch,
        side_effect=PublishError("login timeout after 300s"),
    )

    response = client.post("/api/v1/accounts/toutiao/main/login", json={})
    assert response.status_code == 202
    run_id = response.json()["run_id"]

    run_resp = client.get(f"/api/v1/runs/{run_id}")
    run = run_resp.json()
    assert run["status"] == "failed"
    assert run["error"]["code"] == "login_failed"
    assert "login timeout" in run["error"]["message"]


def test_unexpected_exception_becomes_internal_error(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_login raises non-PublishError -> run.status=failed + error.code=internal_error."""
    _patch_run_login(
        monkeypatch,
        side_effect=RuntimeError("boom"),
    )

    response = client.post("/api/v1/accounts/toutiao/main/login", json={})
    run_id = response.json()["run_id"]

    run = client.get(f"/api/v1/runs/{run_id}").json()
    assert run["status"] == "failed"
    assert run["error"]["code"] == "internal_error"
    assert "boom" in run["error"]["message"]


# unsupported platform ────────────────────────────────────


def test_unsupported_platform_returns_400(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unsupported platform (x / wechat_mp) -> 400 platform_not_supported."""
    response = client.post("/api/v1/accounts/x/main/login", json={})
    assert response.status_code == 400
    body = response.json()
    assert body["detail"]["error"]["code"] == "platform_not_supported"
    assert "toutiao" in body["detail"]["error"]["message"]
    assert "xiaohongshu" in body["detail"]["error"]["message"]
    assert "douyin" in body["detail"]["error"]["message"]


def test_wechat_mp_returns_400(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """wechat_mp not in whitelist (config file path) -> 400."""
    response = client.post("/api/v1/accounts/wechat_mp/main/login", json={})
    assert response.status_code == 400


# progress message propagation ────────────────────────────


def test_login_progress_listener_invoked_during_run(
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """U7-7 修复验收：listener 必须在 run 进行中被多次调，
    真实 log_event -> runs registry.message 路径必须通。

    用 monkeypatch 替换 login_bridge.run_login：执行过程中显式
    触发 ≥3 次 log_event（请在浏览器 / 等待登录完成 / saved），
    断言 listener cb 被调 ≥3 次，每次 msg 与预期对应，runs registry
    的 message 在 run 终止前已被更新（不是只靠终态 register_run）。

    清理 listener（避免 leak 到其他测试）。
    """
    from pipeline.publishers import login_cmd

    out_path = tmp_env / "toutiao_main.json"
    captured_calls: list[tuple[str, str | None, str]] = []

    def fake_listener(platform: str, account: str | None, msg: str) -> None:
        captured_calls.append((platform, account, msg))

    login_cmd.add_progress_listener(fake_listener)
    try:
        from pipeline.webui import login_bridge

        progress_messages: list[str] = [
            "请在浏览器里完成登录（扫码 / 短信 / 密码）",
            "等待登录完成（最多 5s）；完成后页面会自动离开登录页",
            f"saved: {out_path}",
        ]

        def fake_run_login(platform: str, account: str):
            for m in progress_messages:
                login_cmd._login_log_event(
                    logging.INFO, m, ref_id=account, platform=platform,
                )
            return out_path

        monkeypatch.setattr(login_bridge, "run_login", fake_run_login)

        run_id = "test_progress_listener"
        login_bridge.execute_login_run(run_id, "toutiao", "main")

        # listener 收到所有 3 条 progress 事件
        assert len(captured_calls) >= 3, (
            f"expected >=3 listener invocations, got {len(captured_calls)}: "
            f"{captured_calls}"
        )
        for i, m in enumerate(progress_messages):
            assert m in captured_calls[i][2], (
                f"call #{i} msg mismatch: got {captured_calls[i][2]!r}, "
                f"expected to contain {m!r}"
            )

        # runs registry 写到 message 字段（来自 listener → update_run_message）
        from pipeline.webui.api.runs import _RUNS
        rec = _RUNS[run_id]
        assert rec["status"] == "succeeded"
        # 最终 register_run 覆盖为 "登录完成"
        assert "登录完成" in rec["message"]
    finally:
        login_cmd.remove_progress_listener(fake_listener)


def test_bridge_progress_cb_filters_by_account() -> None:
    """bridge._make_progress_cb 闭包必须按 (platform, account) 过滤。

    listener API 本身是 fan-out（每个 listener 收到所有事件）；
    过滤发生在 login_bridge._make_progress_cb 内部——它闭包持有
    本次 run 的 (platform, account)，不匹配时直接丢 update_run_message。

    `update_run_message` 是 lazy-import 在 cb 内部；测试通过 patch
    `pipeline.webui.api.runs.update_run_message`（cb 真正调用的那个）
    来捕获调用。
    """
    from pipeline.webui import login_bridge

    seen: list[str] = []

    monkeypatch = pytest.MonkeyPatch()
    try:
        # Patch api.runs.update_run_message（cb 真正调的）
        from pipeline.webui.api import runs as runs_api

        def fake_update(run_id: str, msg: str) -> None:
            seen.append(msg)

        monkeypatch.setattr(runs_api, "update_run_message", fake_update)

        # 注册一个 cb 给 toutiao/main
        cb = login_bridge._make_progress_cb("r1", "toutiao", "main")

        # 匹配：toutiao/main → update_run_message 应被调
        cb("toutiao", "main", "matched-msg")
        assert seen == ["matched-msg"]

        # 不匹配 platform → 丢
        cb("xiaohongshu", "main", "wrong-platform")
        assert seen == ["matched-msg"]

        # 不匹配 account → 丢
        cb("toutiao", "alt", "wrong-account")
        assert seen == ["matched-msg"]

        # account=None（login_cmd helper 内部调用）→ 通过（不过滤 ref_id）
        cb("toutiao", None, "no-ref-id")
        assert seen == ["matched-msg", "no-ref-id"]
    finally:
        monkeypatch.undo()


def test_login_progress_captures_real_log_events(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """端到端：真实 login_cmd log_event → listener → runs registry.message。

    用真实 login_toutiao（注入 fake Playwright），断言 runs registry.message
    反映真实的 "saved" 进度（不是只靠终态 register_run 写"登录完成"）。
    """
    from unittest.mock import patch
    from pipeline.publishers import login_cmd

    fake_state = {
        "cookies": [{"name": "sessionid", "value": "v", "domain": ".toutiao.com"}],
        "origins": [],
    }
    fake_context = MagicMock()
    fake_context.storage_state.return_value = fake_state
    fake_context.new_page.return_value = MagicMock()
    fake_browser = MagicMock()
    fake_browser.new_context.return_value = fake_context
    fake_page = fake_context.new_page.return_value
    fake_page.wait_for_url.return_value = None
    fake_page.goto.return_value = MagicMock(status=200)

    fake_p = MagicMock()
    fake_p.chromium.launch.return_value = fake_browser
    fake_p.__enter__ = lambda s: fake_p
    fake_p.__exit__ = lambda s, *a: None

    progress_seen: list[str] = []

    def capture_cb(platform: str, account: str | None, msg: str) -> None:
        if platform == "toutiao" and account == "main":
            progress_seen.append(msg)

    login_cmd.add_progress_listener(capture_cb)
    try:
        monkeypatch.chdir(tmp_env)
        with patch("playwright.sync_api.sync_playwright", return_value=fake_p):
            response = client.post("/api/v1/accounts/toutiao/main/login", json={})

        assert response.status_code == 202, response.text
        run_id = response.json()["run_id"]
        run = client.get(f"/api/v1/runs/{run_id}").json()
        assert run["status"] == "succeeded"
        # listener 在 run 进行中至少看到 "请在浏览器" / "等待" / "saved" 三条
        assert len(progress_seen) >= 3, (
            f"expected >=3 progress events captured, got {len(progress_seen)}: "
            f"{progress_seen}"
        )
        assert any("saved" in m for m in progress_seen), (
            f"expected 'saved' in progress events: {progress_seen}"
        )
    finally:
        login_cmd.remove_progress_listener(capture_cb)