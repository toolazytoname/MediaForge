"""Disk-backed fake VideoEngine for restart/idempotency tests. No GPU."""
from __future__ import annotations

import json
import os
from pathlib import Path

from pipeline.creators.video.base import VideoEngine, VideoJobStatus, VideoRequest
from pipeline.utils.errors import CreateError
from pipeline.utils.ids import new_id

_FAKE_BYTES = b"FAKEMP4"


class FakeVideoEngine(VideoEngine):
    name = "fake"

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def submit(self, req: VideoRequest) -> str:
        job_id = new_id("ej")
        self._path(job_id).write_text(
            json.dumps({
                "state": "done",
                "progress": 1.0,
                "error": None,
                "content_id": req.content_id,
                "script": req.script,
                "duration_s": req.duration_s,
                "aspect": req.aspect,
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        return job_id

    def poll(self, job_id: str) -> VideoJobStatus:
        data = self._load(job_id)
        return VideoJobStatus(
            state=str(data.get("state") or "failed"),
            progress=data.get("progress"),
            error=data.get("error"),
        )

    def fetch(self, job_id: str, dest: Path) -> Path:
        self._load(job_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(_FAKE_BYTES)
        return dest

    def set_state(self, job_id: str, *, state: str, progress: float | None = None, error: str | None = None) -> None:
        data = self._load(job_id)
        data["state"] = state
        data["progress"] = progress
        data["error"] = error
        self._path(job_id).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def _path(self, job_id: str) -> Path:
        return self._root / f"{job_id}.json"

    def _load(self, job_id: str) -> dict:
        path = self._path(job_id)
        if not path.is_file():
            raise CreateError(f"fake video job {job_id} not found")
        return json.loads(path.read_text(encoding="utf-8"))


def build_fake_engine(cfg) -> FakeVideoEngine:
    video = getattr(cfg, "video", None)
    root = getattr(video, "fake_root", None) or os.environ.get("MEDIAFORGE_FAKE_VIDEO_ROOT")
    if not root:
        root = "var/fake_video_jobs"
    return FakeVideoEngine(root)


__all__ = ["FakeVideoEngine", "build_fake_engine"]
