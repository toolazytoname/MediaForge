import json
import os

import pytest
import yaml
from fastapi.testclient import TestClient

from pipeline.creators import image_gen, llm
from pipeline.env_keys import IMAGE_ENV_VARS, LLM_ENV_VARS, LLM_PROVIDER_ENV
from pipeline.webui import deps
from pipeline.webui.api import byok_settings
from pipeline.webui.app import create_app

# Placeholder AppID: includes letters outside 0-9a-f so GitHub secret scanning
# does not treat it as a real Tencent WeChat App ID (wx + 16 hex).
_WECHAT_APP = {"app_id": "wxFAKEAPPID0000001", "app_secret": "wx-private-value"}
_MINIMAL_CONFIG = """\
timezone: Asia/Shanghai
pillars:
  - id: ai_daily
    name: AI
    description: d
    scoring_hint: s
sources: []
llm: {tiers: {cheap: x, creative: y, critical: z}}
budget: {monthly_usd: 80.0}
publish:
  enabled: false
  allowed_platforms: []
"""


def _clear_provider_credentials() -> None:
    names = set(LLM_ENV_VARS) | set(IMAGE_ENV_VARS) | {LLM_PROVIDER_ENV}
    names.update(key for key in os.environ if key.startswith("OPENAI_"))
    for name in names:
        os.environ.pop(name, None)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "environ", os.environ.copy())
    _clear_provider_credentials()
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(_MINIMAL_CONFIG, encoding="utf-8")
    monkeypatch.setattr(byok_settings, "_ENV_PATH", tmp_path / "env.json")
    monkeypatch.setattr(byok_settings, "_WECHAT_PATH", tmp_path / "wechat.json")
    monkeypatch.setattr(deps, "_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setattr(deps, "_CONFIG_PATH", str(cfg_path))
    monkeypatch.setattr(llm, "_PROVIDER", llm.MockProvider())
    monkeypatch.setattr(image_gen, "_PROVIDER", None)
    return TestClient(create_app())


def _publish_section(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))["publish"]


def config(**overrides):
    return {
        "base_url": "https://relay.example/v1/", "text_model": "gpt-5.6-sol",
        "image_model": "gpt-image-2", "wire_api": "responses",
        "api_key": "private-test-key", "input_price": 4, "output_price": 20,
        **overrides,
    }


def test_byok_atomic_save_restore_and_no_plaintext(client, tmp_path, monkeypatch):
    response = client.put("/api/v1/settings/byok", json=config())
    assert response.status_code == 200, response.text
    assert "private-test-key" not in response.text
    assert response.json()["base_url"] == "https://relay.example/v1"
    data = json.loads((tmp_path / "env.json").read_text())
    assert data["OPENAI_API_KEY"] == "private-test-key"
    assert data["OPENAI_IMAGE_BASE_URL"] == "https://relay.example/v1"
    assert (tmp_path / "env.json").stat().st_mode & 0o777 == 0o600
    assert image_gen._PROVIDER._model == "gpt-image-2"
    assert llm._PROVIDER._wire_api == "responses"
    assert llm._resolve_model("creative") == "gpt-5.6-sol"
    monkeypatch.delenv("OPENAI_BASE_URL")
    from pipeline.env_keys import load_env_secrets
    load_env_secrets(tmp_path / "env.json")
    assert os.environ["OPENAI_BASE_URL"] == "https://relay.example/v1"
    assert "private-test-key" not in client.get("/api/v1/settings/byok").text


@pytest.mark.parametrize("overrides", [
    {"base_url": "https://name:secret@relay.example/v1"},
    {"base_url": "https://relay.example/v1?key=secret"},
    {"wire_api": "unknown"}, {"input_price": -1}, {"text_model": ""},
])
def test_invalid_configuration_does_not_write(client, tmp_path, overrides):
    assert client.put("/api/v1/settings/byok", json=config(**overrides)).status_code == 400
    assert not (tmp_path / "env.json").exists()


def test_key_deletion_clears_live_providers(client):
    client.put("/api/v1/settings/byok", json=config())
    assert client.delete("/api/v1/settings/byok/key").status_code == 200
    assert isinstance(llm._PROVIDER, llm.MockProvider)
    assert image_gen._PROVIDER is None
    assert not client.get("/api/v1/settings/byok").json()["key_set"]


@pytest.mark.parametrize("env_name, value", [
    ("MINIMAX_IMAGE_API_KEY", "minimax-image-key"),
    ("MINIMAX_API_KEY", "minimax-chat-key"),
])
def test_key_deletion_falls_back_to_minimax_when_image_key_remains(client, monkeypatch, env_name, value):
    monkeypatch.setenv(env_name, value)
    client.put("/api/v1/settings/byok", json=config())
    assert isinstance(image_gen._PROVIDER, image_gen.OpenAIImageProvider)
    assert client.delete("/api/v1/settings/byok/key").status_code == 200
    assert isinstance(llm._PROVIDER, llm.MockProvider)
    assert isinstance(image_gen._PROVIDER, image_gen.MiniMaxImageProvider)
    assert image_gen._PROVIDER._api_key == value
    assert not client.get("/api/v1/settings/byok").json()["key_set"]


def test_byok_defaults_match_live_provider(client, monkeypatch):
    body = client.get("/api/v1/settings/byok").json()
    assert body["text_model"] == "gpt-5.6-sol"
    assert body["wire_api"] == "responses"
    assert body["cost_kind"] == "estimate"
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    provider = llm.OpenAIProvider.from_env("openai")
    assert provider._model == body["text_model"]
    assert provider._wire_api == body["wire_api"]


def test_save_preserves_unrelated_secrets(client, tmp_path):
    (tmp_path / "env.json").write_text('{"MINIMAX_API_KEY": "keep-me"}\n', encoding="utf-8")
    assert client.put("/api/v1/settings/byok", json=config()).status_code == 200
    data = json.loads((tmp_path / "env.json").read_text())
    assert data["MINIMAX_API_KEY"] == "keep-me"
    assert data["OPENAI_API_KEY"] == "private-test-key"


def test_check_requires_key(client):
    response = client.post("/api/v1/settings/byok/check")
    assert response.json()["ok"] is False
    assert "API key" in response.json()["message"]


def test_check_missing_model_is_visible(client, monkeypatch):
    client.put("/api/v1/settings/byok", json=config())
    class Fake:
        def raise_for_status(self):
            return None
        def json(self):
            return {"data": [{"id": "other-model"}]}
    monkeypatch.setattr("httpx.get", lambda *args, **kwargs: Fake())
    response = client.post("/api/v1/settings/byok/check")
    body = response.json()
    assert body["ok"] is False
    assert "gpt-5.6-sol" in body["message"]
    assert "gpt-image-2" in body["missing_models"]


def test_check_unpriced_is_visible(client, monkeypatch):
    client.put("/api/v1/settings/byok", json=config(text_model="private-model", input_price=None, output_price=None))
    class Fake:
        def raise_for_status(self):
            return None
        def json(self):
            return {"data": [{"id": "private-model"}, {"id": "gpt-image-2"}]}
    monkeypatch.setattr("httpx.get", lambda *args, **kwargs: Fake())
    response = client.post("/api/v1/settings/byok/check")
    body = response.json()
    assert body["ok"] is True
    assert body["priced"] is False
    assert "单价" in body["message"]


def test_check_retries_when_socks_extra_missing(client, monkeypatch):
    client.put("/api/v1/settings/byok", json=config())
    class Fake:
        def raise_for_status(self):
            return None
        def json(self):
            return {"data": [{"id": "gpt-5.6-sol"}, {"id": "gpt-image-2"}]}
    def boom(*args, **kwargs):
        raise ImportError("socksio")
    class Client:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def get(self, *args, **kwargs):
            return Fake()
    monkeypatch.setattr("httpx.get", boom)
    monkeypatch.setattr("httpx.Client", Client)
    body = client.post("/api/v1/settings/byok/check").json()
    assert body["ok"] is True
    assert "真实写稿" in body["message"]


def test_check_does_not_leak_key(client, monkeypatch):
    client.put("/api/v1/settings/byok", json=config())
    def boom(*args, **kwargs):
        raise RuntimeError("Authorization Bearer private-test-key")
    monkeypatch.setattr("httpx.get", boom)
    response = client.post("/api/v1/settings/byok/check")
    assert "private-test-key" not in response.text
    assert response.json()["ok"] is False


def test_unknown_model_requires_prices_before_spending(client):
    result = client.put("/api/v1/settings/byok", json=config(text_model="private-model", input_price=None, output_price=None))
    assert result.status_code == 200
    assert result.json()["priced"] is False
    with pytest.raises(llm.UnpricedModelError):
        llm._estimate_cost_usd(llm._resolve_model("creative"), "test", 100)


def test_wechat_save_is_private_and_retains_secret_when_omitted(client, tmp_path):
    response = client.put("/api/v1/settings/wechat", json=_WECHAT_APP)
    assert response.status_code == 200
    assert "wx-private-value" not in response.text
    assert client.get("/api/v1/settings/wechat").json()["configured"] is True
    response = client.put("/api/v1/settings/wechat", json={"app_id": _WECHAT_APP["app_id"]})
    assert response.status_code == 200
    assert json.loads((tmp_path / "wechat.json").read_text())["app_secret"] == "wx-private-value"
    assert (tmp_path / "wechat.json").stat().st_mode & 0o777 == 0o600


def test_wechat_check_redacts_upstream_secret(client, monkeypatch):
    client.put("/api/v1/settings/wechat", json=_WECHAT_APP)
    def fail(*args, **kwargs):
        raise ValueError("secret=wx-private-value access_token=upstream-token")
    monkeypatch.setattr(byok_settings.WechatMpPublisher, "_ensure_access_token", fail)
    response = client.post("/api/v1/settings/wechat/check")
    assert "wx-private-value" not in response.text
    assert "upstream-token" not in response.text
    assert response.json()["ok"] is False


def test_wechat_saves_to_config_referenced_path(client, tmp_path):
    cfg = tmp_path / "config.yaml"
    secret_path = tmp_path / "from-config.json"
    cfg.write_text(
        _MINIMAL_CONFIG
        + "platforms:\n  wechat_mp:\n    kind: api\n    windows: ['09:00-11:00']\n"
        + "    accounts:\n      - id: main\n        credentials: "
        + str(secret_path)
        + "\n",
        encoding="utf-8",
    )
    response = client.put("/api/v1/settings/wechat", json=_WECHAT_APP)
    assert response.status_code == 200, response.text
    assert response.json()["credentials_path"] == str(secret_path)
    assert secret_path.is_file()
    assert json.loads(secret_path.read_text())["app_secret"] == "wx-private-value"
    assert not (tmp_path / "wechat.json").exists()


def test_enable_wechat_without_credentials_does_not_change_config(client, tmp_path):
    cfg = tmp_path / "config.yaml"
    original = cfg.read_bytes()
    response = client.post("/api/v1/settings/wechat/enable")
    assert response.status_code == 400
    assert "凭据" in response.json()["detail"]["error"]["message"]
    assert cfg.read_bytes() == original
    assert _publish_section(cfg) == {"enabled": False, "allowed_platforms": []}


def test_enable_wechat_whitelists_only_wechat_mp(client, tmp_path):
    client.put("/api/v1/settings/wechat", json=_WECHAT_APP)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        _MINIMAL_CONFIG.replace(
            "allowed_platforms: []",
            "allowed_platforms: [toutiao, xiaohongshu]",
        ),
        encoding="utf-8",
    )
    response = client.post("/api/v1/settings/wechat/enable")
    assert response.status_code == 200, response.text
    assert response.json()["delivery_enabled"] is True
    publish = _publish_section(cfg)
    assert publish["enabled"] is True
    assert publish["allowed_platforms"] == ["wechat_mp"]
    assert "toutiao" not in publish["allowed_platforms"]
    assert "xiaohongshu" not in publish["allowed_platforms"]


def test_enable_wechat_missing_config_returns_error(client, tmp_path, monkeypatch):
    client.put("/api/v1/settings/wechat", json=_WECHAT_APP)
    missing = tmp_path / "missing-config.yaml"
    monkeypatch.setattr(deps, "_CONFIG_PATH", str(missing))
    response = client.post("/api/v1/settings/wechat/enable")
    assert response.status_code == 400
    message = response.json()["detail"]["error"]["message"]
    assert "配置文件" in message
    assert not missing.exists()
