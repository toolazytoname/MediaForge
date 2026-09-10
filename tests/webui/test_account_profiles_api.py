from fastapi.testclient import TestClient

from pipeline.account_profiles import create_profile
from pipeline.projects import create_project
from pipeline.webui import deps
from pipeline.webui.api import account_profiles as profiles_api
from pipeline.webui.api import projects as projects_api
from pipeline.webui.app import create_app


NOW = "2026-09-10T04:00:00+00:00"


def test_binding_api_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setattr(projects_api, "_PROJECTS_ROOT", tmp_path / "projects")
    monkeypatch.setattr(profiles_api, "DEFAULT_ACCOUNTS_ROOT", tmp_path / "accounts")
    create_project(
        title="项目", idea="想法", audience="读者", goal="文章", voice="清晰",
        autonomy="collaborate", now=NOW, project_id="prj_apiacct1",
        projects_root=tmp_path / "projects",
    )
    profile = create_profile(
        platform="wechat_mp", display_name="主号", positioning="定位",
        audience="读者", style="克制", references=(), creation_mode="assisted",
        frequency="off", timezone="Asia/Shanghai", budget_usd=0,
        delivery_target="draft", credentials_ref="secrets/wechat_mp_main.json",
        config_account_id="main", now=NOW, accounts_root=tmp_path / "accounts",
        profile_id="acc_api00001",
    )
    client = TestClient(create_app())
    missing = client.get("/api/v1/projects/prj_apiacct1/account-binding")
    assert missing.status_code == 404
    saved = client.put(
        "/api/v1/projects/prj_apiacct1/account-binding",
        json={"platform": "wechat_mp", "account_id": profile.id},
    )
    assert saved.status_code == 200
    assert saved.json()["items"][0]["account_id"] == profile.id
    loaded = client.get("/api/v1/projects/prj_apiacct1/account-binding")
    assert loaded.status_code == 200
    assert loaded.json()["items"][0]["account_id"] == profile.id
