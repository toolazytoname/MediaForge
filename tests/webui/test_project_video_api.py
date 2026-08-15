from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pipeline import master_documents, projects as project_store
from pipeline.project_video import ProjectVideo, VideoShot
from pipeline.webui import deps
from pipeline.webui.api import project_video as video_api
from pipeline.webui.api import projects as projects_api
from pipeline.webui.app import create_app


NOW = "2026-08-15T12:00:00+00:00"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setattr(projects_api, "_PROJECTS_ROOT", tmp_path / "projects")
    return TestClient(create_app())


def test_get_video_empty_then_generate(client, tmp_path, monkeypatch):
    root = tmp_path / "projects"
    project_store.create_project(
        title="标题", idea="想法", audience="读者", goal="视频", voice="克制",
        autonomy="draft", now=NOW, project_id="prj_vidapi", projects_root=root,
    )
    master_documents.save_manual(
        "prj_vidapi", title="标题", body="正文足够长了。", now=NOW, projects_root=root,
    )

    empty = client.get("/api/v1/projects/prj_vidapi/video")
    assert empty.status_code == 200
    assert empty.json() == {"video": None}

    def fake_generate(project_id, **kwargs):
        return ProjectVideo(
            project_id=project_id,
            title="标题",
            script="钩子。判断。收束。",
            duration_s=18,
            aspect="16:9",
            shots=(
                VideoShot(1, "钩子。", "video/shot1.mp4"),
                VideoShot(2, "判断。", "video/shot2.mp4"),
                VideoShot(3, "收束。", "video/shot3.mp4"),
            ),
            file_path="video/preview.mp4",
            updated_at=NOW,
        )

    monkeypatch.setattr(video_api.project_video, "generate_project_video", fake_generate)
    created = client.post("/api/v1/projects/prj_vidapi/video", json={})
    assert created.status_code == 200
    payload = created.json()
    assert payload["file_url"] == "/output/projects/prj_vidapi/video/preview.mp4"
    assert payload["published"] is False
    assert payload["destination"] is None
    assert payload["script"].startswith("钩子")


def test_generate_video_without_master_returns_400(client, tmp_path, monkeypatch):
    root = tmp_path / "projects"
    project_store.create_project(
        title="空", idea="还没写", audience="读者", goal="视频", voice="克制",
        autonomy="draft", now=NOW, project_id="prj_empty", projects_root=root,
    )
    from pipeline.project_video import ProjectVideoError

    def boom(project_id, **kwargs):
        raise ProjectVideoError("先写完文章，再生成视频口播")

    monkeypatch.setattr(video_api.project_video, "generate_project_video", boom)
    response = client.post("/api/v1/projects/prj_empty/video", json={})
    assert response.status_code == 400
    assert response.json()["detail"]["error"]["code"] == "project_video_failed"
