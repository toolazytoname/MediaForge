"""Settings can inspect an OpenAI-compatible relay without exposing secrets."""
from __future__ import annotations

import io
from urllib import error

from fastapi.testclient import TestClient

from pipeline.webui.api import settings
from pipeline.webui.app import create_app


class _Response:
    def __init__(self, payload: bytes) -> None: self._payload = payload
    def read(self) -> bytes: return self._payload
    def __enter__(self): return self
    def __exit__(self, *args): return False


def test_discovery_returns_only_valid_model_identifiers(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "_ENV_SECRETS_PATH", tmp_path / "env.json")
    monkeypatch.setenv("OPENAI_API_KEY", "secret-never-return")
    monkeypatch.setenv("OPENAI_IMAGE_BASE_URL", "https://relay.example/v1")
    monkeypatch.setattr(settings.request, "urlopen", lambda req, timeout: _Response(b'{"data":[{"id":"gpt-image-1"},{"id":"relay-image-v2"},{"id":"bad/path"},{"id":7}]}'))
    response = TestClient(create_app()).get("/api/v1/settings/openai-image-models")
    assert response.status_code == 200
    assert response.json() == {"models": ["gpt-image-1", "relay-image-v2"], "source": "relay"}
    assert "secret-never-return" not in response.text
    assert "relay.example" not in response.text


def test_discovery_requires_relay_and_maps_remote_failure_without_body(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_IMAGE_BASE_URL", raising=False)
    client = TestClient(create_app())
    missing = client.get("/api/v1/settings/openai-image-models")
    assert missing.status_code == 409
    assert missing.json()["detail"]["error"]["code"] == "image_model_discovery_unavailable"

    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("OPENAI_IMAGE_BASE_URL", "https://relay.example/v1")
    def unavailable(req, timeout):
        raise error.HTTPError(req.full_url, 401, "x", {}, io.BytesIO(b'{"error":{"message":"secret"}}'))
    monkeypatch.setattr(settings.request, "urlopen", unavailable)
    failed = client.get("/api/v1/settings/openai-image-models")
    assert failed.status_code == 502
    assert failed.json()["detail"]["error"]["code"] == "image_model_discovery_failed"
    assert "secret" not in failed.text
