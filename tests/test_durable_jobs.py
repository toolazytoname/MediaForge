"""LAZY-59 durable video jobs: restart, idempotency, cost NULL, cancel/timeout."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from pipeline import db, deliverables, projects
from pipeline.creators.video.base import VideoEngine, VideoJobStatus, VideoRequest
from pipeline.jobs import store as job_store
from pipeline.models import Content, ContentStatus, Topic, TopicStatus
from pipeline.sources.dedup import content_hash
from pipeline.utils.errors import UnpricedModelError
from pipeline.webui import video_bridge
from tests.repo_root import REPO_ROOT


class FakeConfig:
    def __init__(self, fake_root: str | Path | None = None):
        self.video = type("V", (), {"fake_root": str(fake_root) if fake_root else None})()


class ScriptedEngine(VideoEngine):
    name = "mpt"

    def __init__(self, *, submit_fn=None, poll_fn=None, fetch_fn=None):
        self.submit_calls = 0
        self.poll_calls = 0
        self.fetch_calls = 0
        self._submit_fn = submit_fn or (lambda req: "engine_job_1")
        self._poll_fn = poll_fn or (
            lambda job_id: VideoJobStatus(state="running", progress=None, error=None)
        )
        self._fetch_fn = fetch_fn or (
            lambda job_id, dest: dest.write_bytes(b"FAKEMP4") or dest
        )

    def submit(self, req: VideoRequest) -> str:
        self.submit_calls += 1
        return self._submit_fn(req)

    def poll(self, job_id: str) -> VideoJobStatus:
        self.poll_calls += 1
        return self._poll_fn(job_id)

    def fetch(self, job_id: str, dest: Path) -> Path:
        self.fetch_calls += 1
        return self._fetch_fn(job_id, dest)


def _open_db(tmp_path) -> sqlite3.Connection:
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    return conn


def _seed_content(tmp_path, conn, *, id="c_job", status=ContentStatus.GATED) -> Content:
    topic_id = f"t_{id[2:]}"
    topic = Topic(
        id=topic_id, source="rss:test", title="标题", url=None, summary=None,
        content_hash=content_hash(id, None), pillar="ai", score=8.0,
        score_reason="ok", status=TopicStatus.CONSUMED.value,
        created_at="2026-08-17T01:00:00+00:00",
        updated_at="2026-08-17T01:00:00+00:00",
    )
    db.insert_topic(conn, topic)
    out_dir = tmp_path / "output" / "2026-08-17" / id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "canonical.md").write_text("# 标题\n\n正文", encoding="utf-8")
    content = Content(
        id=id, topic_id=topic_id, pillar="ai", title="标题",
        canonical_path=str(out_dir / "canonical.md"),
        formats=(), gate_score_total=27.0,
        gate_scores={"info": 9, "fun": 9, "view": 9}, gate_verdict="好",
        status=status,
        created_at="2026-08-17T01:00:00+00:00",
        updated_at="2026-08-17T01:00:00+00:00",
    )
    db.insert_content(conn, content)
    return content


def _count_jobs(conn) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM durable_jobs").fetchone()["n"]


def test_video_bridge_redacts_nested_style_secrets(tmp_path, monkeypatch):
    conn = _open_db(tmp_path)
    content = _seed_content(tmp_path, conn, id="c_redact")
    engine = ScriptedEngine()
    monkeypatch.setitem(video_bridge._ENGINE_BUILDERS, "mpt", lambda cfg: engine)
    job = video_bridge.submit_video_job(
        conn, FakeConfig(), content.id, "mpt", "口播", 8, "9:16",
        {"auth": {"api_key": "nested-secret"}, "mood": "calm"},
        idempotency_key="k-style",
    )
    stored = job_store.get_job(conn, job["job_id"])
    assert stored is not None
    payload = json.loads(stored.request_json)
    assert payload["style"]["auth"]["api_key"] == "[redacted]"
    assert payload["style"]["mood"] == "calm"
    assert "nested-secret" not in stored.request_json
    conn.close()


def test_nested_style_and_auth_secrets_are_redacted(tmp_path):
    conn = _open_db(tmp_path)
    job = job_store.insert_job(
        conn, kind="video_render", idempotency_key="k-nested",
        request={
            "engine_job_id": "ej",
            "api_key": "top-secret",
            "style": {"auth": {"api_key": "nested-secret", "mood": "calm"}},
        },
        engine="fake",
    )
    payload = json.loads(job.request_json)
    assert payload["api_key"] == "[redacted]"
    assert payload["style"]["auth"]["api_key"] == "[redacted]"
    assert payload["style"]["auth"]["mood"] == "calm"
    assert "nested-secret" not in job.request_json
    conn.close()


def test_request_json_is_append_only(tmp_path):
    conn = _open_db(tmp_path)
    job = job_store.insert_job(
        conn, kind="video_render", idempotency_key="k1",
        request={"engine_job_id": "ej"}, engine="fake", content_id="c_x",
    )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute(
            "UPDATE durable_jobs SET request_json=? WHERE id=?",
            ('{"hijack": true}', job.id),
        )
    conn.close()


def test_unknown_cost_refuses_zero(tmp_path):
    conn = _open_db(tmp_path)
    job = job_store.insert_job(
        conn, kind="video_render", idempotency_key="k-cost",
        request={"engine_job_id": "ej"}, engine="mpt",
    )
    with pytest.raises(UnpricedModelError):
        job_store.try_finish_job(
            conn, job.id, state="done", now="2026-08-17T02:00:00+00:00",
            cost_usd=0,
        )
    finished = job_store.try_finish_job(
        conn, job.id, state="done", now="2026-08-17T02:00:00+00:00",
        cost_usd=None, result_path="output/x.mp4",
    )
    assert finished is not None
    assert finished.cost_usd is None
    conn.close()


def test_idempotent_submit_does_not_double_engine_or_asset(tmp_path, monkeypatch):
    conn = _open_db(tmp_path)
    _seed_content(tmp_path, conn, id="c_idem")
    engine = ScriptedEngine(
        poll_fn=lambda jid: VideoJobStatus(state="done", progress=1.0, error=None),
    )
    monkeypatch.setitem(video_bridge._ENGINE_BUILDERS, "mpt", lambda cfg: engine)
    first = video_bridge.submit_video_job(
        conn, FakeConfig(), "c_idem", "mpt", "脚本", 30, "9:16", {},
        idempotency_key="same-key",
    )
    second = video_bridge.submit_video_job(
        conn, FakeConfig(), "c_idem", "mpt", "脚本", 30, "9:16", {},
        idempotency_key="same-key",
    )
    assert first["job_id"] == second["job_id"]
    assert engine.submit_calls == 1
    assert _count_jobs(conn) == 1

    video_bridge.poll_video_job(conn, FakeConfig(), first["job_id"])
    video_bridge.poll_video_job(conn, FakeConfig(), first["job_id"])
    row = conn.execute("SELECT cost_usd, result_path FROM durable_jobs").fetchone()
    assert row["cost_usd"] is None
    assert row["result_path"]
    formats = json.loads(
        conn.execute("SELECT formats FROM contents WHERE id=?", ("c_idem",)).fetchone()["formats"]
    )
    assert formats.count("video_mpt") == 1
    assert engine.fetch_calls == 1
    conn.close()


def test_cancel_then_engine_done_does_not_write_asset(tmp_path, monkeypatch):
    conn = _open_db(tmp_path)
    content = _seed_content(tmp_path, conn, id="c_can")
    engine = ScriptedEngine(
        poll_fn=lambda jid: VideoJobStatus(state="done", progress=1.0, error=None),
    )
    monkeypatch.setitem(video_bridge._ENGINE_BUILDERS, "mpt", lambda cfg: engine)
    job = video_bridge.submit_video_job(
        conn, FakeConfig(), "c_can", "mpt", "脚本", 30, "9:16", {},
    )
    cancelled = video_bridge.cancel_video_job(conn, job["job_id"])
    assert cancelled["state"] == "cancelled"
    again = video_bridge.poll_video_job(conn, FakeConfig(), job["job_id"])
    assert again["state"] == "cancelled"
    assert again["cost_usd"] is None
    dest = Path(content.canonical_path).parent / "video_mpt.mp4"
    assert not dest.exists()
    formats = json.loads(
        conn.execute("SELECT formats FROM contents WHERE id=?", ("c_can",)).fetchone()["formats"]
    )
    assert "video_mpt" not in formats
    conn.close()


def test_timeout_then_engine_done_stays_failed(tmp_path, monkeypatch):
    conn = _open_db(tmp_path)
    content = _seed_content(tmp_path, conn, id="c_to")
    engine = ScriptedEngine(
        poll_fn=lambda jid: VideoJobStatus(state="done", progress=1.0, error=None),
    )
    monkeypatch.setitem(video_bridge._ENGINE_BUILDERS, "mpt", lambda cfg: engine)
    job = video_bridge.submit_video_job(
        conn, FakeConfig(), "c_to", "mpt", "脚本", 30, "9:16", {},
        timeout_s=1,
    )
    result = video_bridge.poll_video_job(
        conn, FakeConfig(), job["job_id"], now="2099-01-01T00:00:00+00:00",
    )
    assert result["state"] == "failed"
    assert result["error"].startswith("timeout:")
    again = video_bridge.poll_video_job(conn, FakeConfig(), job["job_id"])
    assert again["state"] == "failed"
    assert again["error"].startswith("timeout:")
    assert not (Path(content.canonical_path).parent / "video_mpt.mp4").exists()
    conn.close()


def test_video_deliverable_can_attach_job(tmp_path, monkeypatch):
    conn = _open_db(tmp_path)
    _seed_content(tmp_path, conn, id="c_dlv")
    root = tmp_path / "projects"
    projects.create_project(
        title="项目", idea="想法", audience="读者", goal="视频", voice="清晰",
        autonomy="collaborate", now="2026-08-17T00:00:00+00:00",
        project_id="prj_video", projects_root=root,
    )
    item = deliverables.create_video(
        "prj_video", title="短视频", script="口播", duration_s=15, aspect="9:16",
        now="2026-08-17T00:01:00+00:00", engine="fake", projects_root=root,
    )
    engine = ScriptedEngine()
    monkeypatch.setitem(video_bridge._ENGINE_BUILDERS, "mpt", lambda cfg: engine)
    job = video_bridge.submit_video_job(
        conn, FakeConfig(), "c_dlv", "mpt", "口播", 15, "9:16", {},
        project_id="prj_video", deliverable_id=item.id, projects_root=root,
    )
    attached = deliverables.get_deliverable("prj_video", item.id, projects_root=root)
    assert attached.payload["render_job_id"] == job["job_id"]
    assert job["deliverable_id"] == item.id
    conn.close()


def test_in_process_restart_polls_same_job(tmp_path, monkeypatch):
    conn = _open_db(tmp_path)
    content = _seed_content(tmp_path, conn, id="c_rs")
    engine = ScriptedEngine(
        poll_fn=lambda jid: VideoJobStatus(state="done", progress=1.0, error=None),
    )
    monkeypatch.setitem(video_bridge._ENGINE_BUILDERS, "mpt", lambda cfg: engine)
    job = video_bridge.submit_video_job(
        conn, FakeConfig(), "c_rs", "mpt", "脚本", 30, "9:16", {},
    )
    job_id = job["job_id"]
    conn.close()

    conn2 = db.connect(tmp_path / "state.db")
    result = video_bridge.poll_video_job(conn2, FakeConfig(), job_id)
    assert result["job_id"] == job_id
    assert result["state"] == "done"
    assert result["output_path"].endswith("video_mpt.mp4")
    assert (Path(content.canonical_path).parent / "video_mpt.mp4").exists()
    assert result["cost_usd"] is None
    conn2.close()


def test_process_restart_with_fake_engine(tmp_path):
    db_path = tmp_path / "state.db"
    fake_root = tmp_path / "fake_jobs"
    conn = _open_db(tmp_path)
    _seed_content(tmp_path, conn, id="c_proc")
    cfg = FakeConfig(fake_root)
    job = video_bridge.submit_video_job(
        conn, cfg, "c_proc", "fake", "脚本", 20, "9:16", {},
    )
    job_id = job["job_id"]
    assert job["state"] == "queued"
    request = json.loads(
        conn.execute("SELECT request_json FROM durable_jobs WHERE id=?", (job_id,)).fetchone()[0]
    )
    assert isinstance(request["engine_job_id"], str)
    conn.close()

    script = (
        "import json,sys\n"
        "from pipeline import db\n"
        "from pipeline.webui import video_bridge\n"
        f"class Cfg:\n"
        f"    class video:\n"
        f"        fake_root = {str(fake_root)!r}\n"
        f"conn = db.connect({str(db_path)!r})\n"
        f"job = video_bridge.poll_video_job(conn, Cfg(), {job_id!r})\n"
        "print(json.dumps(job))\n"
        "conn.close()\n"
    )
    env = os.environ.copy()
    env["MEDIAFORGE_FAKE_VIDEO_ROOT"] = str(fake_root)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    recovered = json.loads(proc.stdout)
    assert recovered["job_id"] == job_id
    assert recovered["state"] == "done"
    assert recovered["output_path"]
    assert recovered["cost_usd"] is None
    assert Path(recovered["output_path"]).is_file()


def test_existing_video_engines_still_registered():
    assert set(video_bridge._ENGINE_BUILDERS) >= {"mpt", "pixelle", "digitalhuman", "fake"}
