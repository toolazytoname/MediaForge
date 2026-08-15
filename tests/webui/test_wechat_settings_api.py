from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from pipeline.publishers import wechat_mp
from pipeline.webui.api import settings as settings_mod
from pipeline.webui.app import create_app


@pytest.fixture
def client():
    return TestClient(create_app())


def test_wechat_settings_round_trip_never_returns_secret(client, tmp_path, monkeypatch):
    creds = tmp_path / "wechat_mp_main.json"
    monkeypatch.setattr(settings_mod, "_WECHAT_CREDENTIALS_PATH", str(creds))

    empty = client.get("/api/v1/settings/wechat-mp")
    assert empty.status_code == 200
    assert empty.json() == {
        "configured": False,
        "account": "main",
        "app_id_masked": None,
    }

    saved = client.post(
        "/api/v1/settings/wechat-mp",
        json={"app_id": "wx1234567890abcd", "app_secret": "super-secret-value"},
    )
    assert saved.status_code == 200
    assert saved.json()["configured"] is True
    assert saved.json()["app_id_masked"].endswith("abcd")
    assert "super-secret-value" not in saved.text
    stored = json.loads(creds.read_text(encoding="utf-8"))
    assert stored == {"app_id": "wx1234567890abcd", "app_secret": "super-secret-value"}
    assert creds.stat().st_mode & 0o777 == 0o600

    cleared = client.delete("/api/v1/settings/wechat-mp")
    assert cleared.status_code == 200
    assert cleared.json()["configured"] is False
    assert not creds.exists()


def test_wechat_probe_reports_token_success_and_ip_errors(client, tmp_path, monkeypatch):
    creds = tmp_path / "wechat_mp_main.json"
    monkeypatch.setattr(settings_mod, "_WECHAT_CREDENTIALS_PATH", str(creds))
    missing = client.post("/api/v1/settings/wechat-mp/probe")
    assert missing.status_code == 200
    assert missing.json()["ok"] is False

    client.post(
        "/api/v1/settings/wechat-mp",
        json={"app_id": "wx1234567890abcd", "app_secret": "super-secret-value"},
    )

    class FakePublisher:
        def __init__(self, **kwargs):
            pass

        def _ensure_access_token(self):
            return "token"

    monkeypatch.setattr(settings_mod, "WechatMpPublisher", FakePublisher)
    ok = client.post("/api/v1/settings/wechat-mp/probe")
    assert ok.status_code == 200
    assert ok.json()["ok"] is True
    assert "token" not in ok.text.lower() or "access_token" not in ok.text

    class FailPublisher:
        def __init__(self, **kwargs):
            pass

        def _ensure_access_token(self):
            raise wechat_mp.LoginExpired("wechat_mp auth/permission error 40164: invalid ip（出口 IP 需加入公众号后台 IP 白名单）")

    monkeypatch.setattr(settings_mod, "WechatMpPublisher", FailPublisher)
    failed = client.post("/api/v1/settings/wechat-mp/probe")
    assert failed.status_code == 200
    assert failed.json()["ok"] is False
    assert "40164" in failed.json()["message"]
