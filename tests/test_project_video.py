from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline import master_documents, projects as project_store
from pipeline.project_video import (
    ProjectVideoError,
    fallback_script,
    generate_project_video,
    load_video,
    parse_script_payload,
    require_master,
    save_video,
    stitch_clips,
    to_payload,
)


NOW = "2026-08-15T12:00:00+00:00"


def _project(root: Path, project_id: str = "prj_video01"):
    project_store.create_project(
        title="标题", idea="想法", audience="读者", goal="视频", voice="克制",
        autonomy="draft", now=NOW, project_id=project_id, projects_root=root,
    )
    return project_id


def test_save_and_load_video_sidecar(tmp_path):
    root = tmp_path / "projects"
    _project(root)
    master_documents.save_manual(
        "prj_video01", title="标题", body="## 问题\n\n正文。", now=NOW, projects_root=root,
    )
    saved = save_video(
        "prj_video01",
        title="标题",
        script="开头一句。中间一句。收束一句。",
        duration_s=18,
        aspect="16:9",
        shots=(
            {"index": 1, "line": "开头一句。", "file_path": "video/shot1.mp4"},
            {"index": 2, "line": "中间一句。", "file_path": "video/shot2.mp4"},
        ),
        file_path="video/preview.mp4",
        now=NOW,
        projects_root=root,
    )
    loaded = load_video("prj_video01", projects_root=root)
    assert loaded is not None
    assert loaded.file_path == "video/preview.mp4"
    assert loaded.duration_s == 18
    assert len(loaded.shots) == 2
    assert saved.script.startswith("开头")
    payload = to_payload(loaded)
    assert payload["file_url"] == "/output/projects/prj_video01/video/preview.mp4"
    assert payload["published"] is False
    assert payload["destination"] is None


def test_rejects_path_traversal_in_sidecar(tmp_path):
    root = tmp_path / "projects"
    _project(root)
    with pytest.raises(ProjectVideoError, match="invalid video path"):
        save_video(
            "prj_video01",
            title="标题",
            script="口播",
            duration_s=18,
            aspect="16:9",
            shots=({"index": 1, "line": "口播", "file_path": "../secret.mp4"},),
            file_path="video/preview.mp4",
            now=NOW,
            projects_root=root,
        )


def test_require_master_needs_body(tmp_path):
    root = tmp_path / "projects"
    _project(root)
    with pytest.raises(ProjectVideoError, match="先写完文章"):
        require_master("prj_video01", projects_root=root)


def test_parse_script_payload_accepts_fenced_json():
    raw = """```json
{"script":"钩子。判断。收束。","duration_s":18,"shots":[{"line":"钩子。"},{"line":"判断。"},{"line":"收束。"}]}
```"""
    parsed = parse_script_payload(raw)
    assert parsed["script"].startswith("钩子")
    assert len(parsed["shots"]) == 3
    assert parsed["shots"][0]["index"] == 1


def test_parse_script_payload_rejects_too_few_shots():
    with pytest.raises(ValueError, match="3 to 6"):
        parse_script_payload(json.dumps({
            "script": "只有一句。",
            "shots": [{"line": "只有一句。"}],
        }))


def test_fallback_script_uses_article_sentences():
    payload = fallback_script(
        "越想独占越做不成",
        "## 开头\n\n梁文锋说不能独占整盘棋。\n\n![图](/x.png)\n\n开源不是战术，是善意。\n",
    )
    assert len(payload["shots"]) == 3
    assert "独占" in payload["script"]
    assert "开源" in payload["script"]


def test_generate_project_video_stitches_imported_clips(tmp_path):
    root = tmp_path / "projects"
    _project(root)
    master_documents.save_manual(
        "prj_video01",
        title="越想独占越做不成",
        body="## 问题\n\n梁文锋说不能独占整盘棋。\n\n开源不是战术，是善意。\n",
        now=NOW,
        projects_root=root,
    )
    clips = [tmp_path / "a.mp4", tmp_path / "b.mp4", tmp_path / "c.mp4"]
    for clip in clips:
        clip.write_bytes(b"fake")

    def fake_complete(_prompt: str) -> str:
        return json.dumps({
            "script": "钩子。判断。收束。",
            "duration_s": 18,
            "shots": [{"line": "钩子。"}, {"line": "判断。"}, {"line": "收束。"}],
        })

    def fake_stitch(paths, dest, **_kwargs):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"joined")
        return dest

    pack = generate_project_video(
        "prj_video01",
        now=NOW,
        projects_root=root,
        complete_fn=fake_complete,
        extra_clips=clips,
        stitch_fn=fake_stitch,
    )
    assert pack.file_path == "video/preview.mp4"
    assert (root / "prj_video01" / "video" / "preview.mp4").read_bytes() == b"joined"
    assert [shot.line for shot in pack.shots] == ["钩子。", "判断。", "收束。"]
    assert pack.shots[0].file_path == "video/shot1.mp4"


def test_stitch_clips_with_ffmpeg(tmp_path):
    sources = []
    for index, color in enumerate(("red", "blue"), start=1):
        dest = tmp_path / f"in{index}.mp4"
        completed = pytest.importorskip("subprocess").run(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=320x240:d=0.4",
                "-pix_fmt", "yuv420p", str(dest),
            ],
            check=False,
            capture_output=True,
        )
        if completed.returncode != 0:
            pytest.skip("ffmpeg cannot encode test clips")
        sources.append(dest)
    out = tmp_path / "joined.mp4"
    stitch_clips(sources, out)
    assert out.is_file() and out.stat().st_size > 0
