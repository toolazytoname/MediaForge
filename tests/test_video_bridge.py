"""M12-3 video_bridge 单测。

覆盖（TASKS.md M12-3 验收标准 + prompt 补充要点）：
  - content 不存在 / 状态非法 → ContentNotFoundError / ContentStatusError
  - engine 参数非法 → InvalidEngineError
  - engine 工厂返回 None / 抛异常 → EngineUnavailableError
  - engine.submit() 抛 CreateError（如数字人缺形象模板）→ 原样透传，
    不被误判为 EngineUnavailableError
  - poll 未终态 → 原样返回 state/progress/error，不触发 fetch
  - poll done → 触发 fetch + 写回 contents.formats（仅一次，幂等）
  - poll 404（task lost）→ 记录标记 failed
  - job_id 不存在 → JobNotFoundError
  - script 派生：content 不存在/状态非法 → 对应异常；BudgetExceeded 原样上抛

全部注入假 VideoEngine（不真调 LatentSync/MPT/Pixelle 网络）。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from pipeline import db
from pipeline.creators.llm import CompletionResult, LLMProvider, set_provider
from pipeline.creators.video.base import VideoEngine, VideoJobStatus, VideoRequest
from pipeline.models import Content, ContentStatus, Topic, TopicStatus
from pipeline.sources.dedup import content_hash
from pipeline.utils.errors import BudgetExceeded, CreateError
from pipeline.webui import video_bridge


# ── helpers ──────────────────────────────────────────────────


class ScriptedProvider(LLMProvider):
    def __init__(self, responses: list[str]):
        self._responses = list(responses)

    def call(self, prompt, model, max_tokens):
        if not self._responses:
            raise AssertionError("no scripted response left")
        return CompletionResult(
            text=self._responses.pop(0), input_tokens=100, output_tokens=100,
        )


class BudgetProvider(LLMProvider):
    def call(self, prompt, model, max_tokens):
        raise BudgetExceeded(stage="video_script", used_usd=100.0, limit_usd=5.0)


@pytest.fixture(autouse=True)
def reset_provider():
    set_provider(ScriptedProvider([]))
    yield
    set_provider(ScriptedProvider([]))


@pytest.fixture(autouse=True)
def clear_jobs():
    """每个测试独立的 _JOBS 命名空间——避免用例间残留状态互相污染。"""
    video_bridge._JOBS.clear()
    yield
    video_bridge._JOBS.clear()


class FakeConfig:
    """submit_video_job/derive_video_script 目前不读取 cfg 内容，
    传一个哨兵占位即可（engine builder 被 monkeypatch 掉，不会真的解析）。
    """


def _open_db(tmp_path) -> sqlite3.Connection:
    c = db.connect(tmp_path / "state.db")
    db.init_db(c)
    return c


def _seed_content(
    tmp_path, conn, *, id="c_v01", status=ContentStatus.GATED,
    title="测试标题", body="正文内容" * 200,
) -> Content:
    topic_id = f"t_{id[2:]}"
    topic = Topic(
        id=topic_id, source="rss:test", title=title, url=None, summary=None,
        content_hash=content_hash(title, None), pillar="ai", score=8.0,
        score_reason="ok", status=TopicStatus.CONSUMED.value,
        created_at="2026-07-14T01:00:00+00:00",
        updated_at="2026-07-14T01:00:00+00:00",
    )
    db.insert_topic(conn, topic)
    out_dir = tmp_path / "output" / "2026-07-14" / id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "canonical.md").write_text(f"# {title}\n\n{body}", encoding="utf-8")
    content = Content(
        id=id, topic_id=topic_id, pillar="ai", title=title,
        canonical_path=str(out_dir / "canonical.md"),
        formats=(), gate_score_total=27.0,
        gate_scores={"info": 9, "fun": 9, "view": 9}, gate_verdict="好",
        status=status,
        created_at="2026-07-14T01:00:00+00:00",
        updated_at="2026-07-14T01:00:00+00:00",
    )
    db.insert_content(conn, content)
    return content


class FakeEngine(VideoEngine):
    """假引擎：行为由测试注入的回调控制。"""

    name = "fake"

    def __init__(self, *, submit_fn=None, poll_fn=None, fetch_fn=None):
        self._submit_fn = submit_fn or (lambda req: "engine_job_1")
        self._poll_fn = poll_fn or (
            lambda job_id: VideoJobStatus(state="running", progress=None, error=None)
        )
        self._fetch_fn = fetch_fn or (
            lambda job_id, dest: dest.write_bytes(b"FAKEMP4") or dest
        )

    def submit(self, req: VideoRequest) -> str:
        return self._submit_fn(req)

    def poll(self, job_id: str) -> VideoJobStatus:
        return self._poll_fn(job_id)

    def fetch(self, job_id: str, dest: Path) -> Path:
        return self._fetch_fn(job_id, dest)


# ── derive_video_script ──────────────────────────────────────


class TestDeriveVideoScript:
    def test_content_not_found(self, tmp_path):
        conn = _open_db(tmp_path)
        try:
            with pytest.raises(video_bridge.ContentNotFoundError):
                video_bridge.derive_video_script(conn, FakeConfig(), "c_nope", 60)
        finally:
            conn.close()

    def test_wrong_status(self, tmp_path):
        conn = _open_db(tmp_path)
        _seed_content(tmp_path, conn, id="c_done", status=ContentStatus.DONE)
        try:
            with pytest.raises(video_bridge.ContentStatusError):
                video_bridge.derive_video_script(conn, FakeConfig(), "c_done", 60)
        finally:
            conn.close()

    def test_success_returns_llm_text(self, tmp_path):
        conn = _open_db(tmp_path)
        _seed_content(tmp_path, conn, id="c_ok", status=ContentStatus.GATED)
        set_provider(ScriptedProvider(["今天来聊聊这个新工具。"]))
        try:
            script = video_bridge.derive_video_script(conn, FakeConfig(), "c_ok", 60)
        finally:
            conn.close()
        assert script == "今天来聊聊这个新工具。"

    def test_budget_exceeded_propagates(self, tmp_path):
        conn = _open_db(tmp_path)
        _seed_content(tmp_path, conn, id="c_bud", status=ContentStatus.GATED)
        set_provider(BudgetProvider())
        try:
            with pytest.raises(BudgetExceeded):
                video_bridge.derive_video_script(conn, FakeConfig(), "c_bud", 60)
        finally:
            conn.close()


# ── submit_video_job ─────────────────────────────────────────


class TestSubmitVideoJob:
    def test_content_not_found(self, tmp_path):
        conn = _open_db(tmp_path)
        try:
            with pytest.raises(video_bridge.ContentNotFoundError):
                video_bridge.submit_video_job(
                    conn, FakeConfig(), "c_nope", "mpt", "脚本", 60, "9:16", {},
                )
        finally:
            conn.close()

    def test_wrong_status(self, tmp_path):
        conn = _open_db(tmp_path)
        _seed_content(tmp_path, conn, id="c_done2", status=ContentStatus.DONE)
        try:
            with pytest.raises(video_bridge.ContentStatusError):
                video_bridge.submit_video_job(
                    conn, FakeConfig(), "c_done2", "mpt", "脚本", 60, "9:16", {},
                )
        finally:
            conn.close()

    def test_invalid_engine(self, tmp_path):
        conn = _open_db(tmp_path)
        _seed_content(tmp_path, conn, id="c_ie", status=ContentStatus.GATED)
        try:
            with pytest.raises(video_bridge.InvalidEngineError):
                video_bridge.submit_video_job(
                    conn, FakeConfig(), "c_ie", "not_a_real_engine",
                    "脚本", 60, "9:16", {},
                )
        finally:
            conn.close()

    def test_engine_builder_returns_none_raises_unavailable(
        self, tmp_path, monkeypatch,
    ):
        conn = _open_db(tmp_path)
        _seed_content(tmp_path, conn, id="c_none", status=ContentStatus.GATED)
        monkeypatch.setitem(
            video_bridge._ENGINE_BUILDERS, "mpt", lambda cfg: None,
        )
        try:
            with pytest.raises(video_bridge.EngineUnavailableError):
                video_bridge.submit_video_job(
                    conn, FakeConfig(), "c_none", "mpt", "脚本", 60, "9:16", {},
                )
        finally:
            conn.close()

    def test_engine_builder_raises_wrapped_as_unavailable(
        self, tmp_path, monkeypatch,
    ):
        conn = _open_db(tmp_path)
        _seed_content(tmp_path, conn, id="c_boom", status=ContentStatus.GATED)

        def boom(cfg):
            raise RuntimeError("service not running")

        monkeypatch.setitem(video_bridge._ENGINE_BUILDERS, "pixelle", boom)
        try:
            with pytest.raises(video_bridge.EngineUnavailableError, match="service not running"):
                video_bridge.submit_video_job(
                    conn, FakeConfig(), "c_boom", "pixelle", "脚本", 60, "9:16", {},
                )
        finally:
            conn.close()

    def test_engine_submit_create_error_propagates(self, tmp_path, monkeypatch):
        """engine.submit() 的业务性失败（如数字人缺形象模板）原样透传，
        不被误判/吞成 EngineUnavailableError。"""
        conn = _open_db(tmp_path)
        _seed_content(tmp_path, conn, id="c_dh", status=ContentStatus.GATED)

        def raise_create_error(req):
            raise CreateError("avatar_template 'x' not configured")

        fake_eng = FakeEngine(submit_fn=raise_create_error)
        monkeypatch.setitem(
            video_bridge._ENGINE_BUILDERS, "digitalhuman", lambda cfg: fake_eng,
        )
        try:
            with pytest.raises(CreateError, match="avatar_template"):
                video_bridge.submit_video_job(
                    conn, FakeConfig(), "c_dh", "digitalhuman",
                    "脚本", 60, "9:16", {"avatar_template": "x"},
                )
        finally:
            conn.close()

    def test_success_returns_public_job_without_internal_fields(
        self, tmp_path, monkeypatch,
    ):
        conn = _open_db(tmp_path)
        _seed_content(tmp_path, conn, id="c_sub", status=ContentStatus.GATED)
        fake_eng = FakeEngine(submit_fn=lambda req: "ej_1")
        monkeypatch.setitem(
            video_bridge._ENGINE_BUILDERS, "mpt", lambda cfg: fake_eng,
        )
        try:
            job = video_bridge.submit_video_job(
                conn, FakeConfig(), "c_sub", "mpt", "脚本", 60, "9:16", {},
            )
        finally:
            conn.close()
        assert job["content_id"] == "c_sub"
        assert job["engine"] == "mpt"
        assert job["state"] == "submitted"
        assert job["output_url"] is None
        assert not any(k.startswith("_") for k in job)
        assert job["job_id"] in video_bridge._JOBS


# ── poll_video_job ────────────────────────────────────────────


class TestPollVideoJob:
    def test_job_not_found(self, tmp_path):
        conn = _open_db(tmp_path)
        try:
            with pytest.raises(video_bridge.JobNotFoundError):
                video_bridge.poll_video_job(conn, FakeConfig(), "vjob_nope")
        finally:
            conn.close()

    def test_poll_not_terminal_returns_state(self, tmp_path, monkeypatch):
        conn = _open_db(tmp_path)
        _seed_content(tmp_path, conn, id="c_p1", status=ContentStatus.GATED)
        fake_eng = FakeEngine(
            poll_fn=lambda jid: VideoJobStatus(state="running", progress=None, error=None),
        )
        monkeypatch.setitem(video_bridge._ENGINE_BUILDERS, "mpt", lambda cfg: fake_eng)
        job = video_bridge.submit_video_job(
            conn, FakeConfig(), "c_p1", "mpt", "脚本", 60, "9:16", {},
        )
        try:
            result = video_bridge.poll_video_job(conn, FakeConfig(), job["job_id"])
        finally:
            conn.close()
        assert result["state"] == "running"
        assert result["output_path"] is None

    def test_poll_done_triggers_fetch_and_updates_formats(
        self, tmp_path, monkeypatch,
    ):
        conn = _open_db(tmp_path)
        content = _seed_content(tmp_path, conn, id="c_p2", status=ContentStatus.GATED)
        fake_eng = FakeEngine(
            poll_fn=lambda jid: VideoJobStatus(state="done", progress=None, error=None),
        )
        monkeypatch.setitem(video_bridge._ENGINE_BUILDERS, "mpt", lambda cfg: fake_eng)
        job = video_bridge.submit_video_job(
            conn, FakeConfig(), "c_p2", "mpt", "脚本", 60, "9:16", {},
        )
        result = video_bridge.poll_video_job(conn, FakeConfig(), job["job_id"])
        conn.close()

        assert result["state"] == "done"
        assert result["output_path"].endswith("video_mpt.mp4")
        assert result["output_url"].endswith("/c_p2/video_mpt.mp4")
        out_dir = Path(content.canonical_path).parent
        assert (out_dir / "video_mpt.mp4").exists()

        conn2 = db.connect(tmp_path / "state.db")
        try:
            row = conn2.execute(
                "SELECT formats FROM contents WHERE id=?", ("c_p2",),
            ).fetchone()
            formats = json.loads(row["formats"])
            assert "video_mpt" in formats
        finally:
            conn2.close()

    def test_poll_done_is_idempotent_not_refetched(self, tmp_path, monkeypatch):
        """第二次 poll 已是 done 状态 → 直接返回，不重复调用 engine.poll/fetch。"""
        conn = _open_db(tmp_path)
        _seed_content(tmp_path, conn, id="c_p3", status=ContentStatus.GATED)
        calls = {"poll": 0, "fetch": 0}

        def poll_fn(jid):
            calls["poll"] += 1
            return VideoJobStatus(state="done", progress=None, error=None)

        def fetch_fn(jid, dest):
            calls["fetch"] += 1
            dest.write_bytes(b"FAKEMP4")
            return dest

        fake_eng = FakeEngine(poll_fn=poll_fn, fetch_fn=fetch_fn)
        monkeypatch.setitem(video_bridge._ENGINE_BUILDERS, "mpt", lambda cfg: fake_eng)
        job = video_bridge.submit_video_job(
            conn, FakeConfig(), "c_p3", "mpt", "脚本", 60, "9:16", {},
        )
        video_bridge.poll_video_job(conn, FakeConfig(), job["job_id"])
        video_bridge.poll_video_job(conn, FakeConfig(), job["job_id"])
        conn.close()

        assert calls["poll"] == 1
        assert calls["fetch"] == 1

    def test_poll_task_lost_marks_failed(self, tmp_path, monkeypatch):
        """engine.poll 抛 CreateError（如 404 task lost）→ 记录标记 failed。"""
        conn = _open_db(tmp_path)
        _seed_content(tmp_path, conn, id="c_p4", status=ContentStatus.GATED)

        def poll_fn(jid):
            raise CreateError("digitalhuman task lost (404)")

        fake_eng = FakeEngine(poll_fn=poll_fn)
        monkeypatch.setitem(video_bridge._ENGINE_BUILDERS, "mpt", lambda cfg: fake_eng)
        job = video_bridge.submit_video_job(
            conn, FakeConfig(), "c_p4", "mpt", "脚本", 60, "9:16", {},
        )
        result = video_bridge.poll_video_job(conn, FakeConfig(), job["job_id"])
        conn.close()

        assert result["state"] == "failed"
        assert "task lost" in result["error"]

    def test_poll_fetch_failure_marks_failed(self, tmp_path, monkeypatch):
        """poll 返回 done 但 fetch 失败（CreateError）→ 标记 failed。"""
        conn = _open_db(tmp_path)
        _seed_content(tmp_path, conn, id="c_p5", status=ContentStatus.GATED)

        def fetch_fn(jid, dest):
            raise CreateError("no usable output")

        fake_eng = FakeEngine(
            poll_fn=lambda jid: VideoJobStatus(state="done", progress=None, error=None),
            fetch_fn=fetch_fn,
        )
        monkeypatch.setitem(video_bridge._ENGINE_BUILDERS, "mpt", lambda cfg: fake_eng)
        job = video_bridge.submit_video_job(
            conn, FakeConfig(), "c_p5", "mpt", "脚本", 60, "9:16", {},
        )
        result = video_bridge.poll_video_job(conn, FakeConfig(), job["job_id"])
        conn.close()

        assert result["state"] == "failed"
        assert "no usable output" in result["error"]

    def test_poll_progress_passed_through_unchanged(self, tmp_path, monkeypatch):
        """progress 语义不由 bridge 篡改——原样传递引擎返回值。"""
        conn = _open_db(tmp_path)
        _seed_content(tmp_path, conn, id="c_p6", status=ContentStatus.GATED)
        fake_eng = FakeEngine(
            poll_fn=lambda jid: VideoJobStatus(state="running", progress=0.42, error=None),
        )
        monkeypatch.setitem(video_bridge._ENGINE_BUILDERS, "mpt", lambda cfg: fake_eng)
        job = video_bridge.submit_video_job(
            conn, FakeConfig(), "c_p6", "mpt", "脚本", 60, "9:16", {},
        )
        result = video_bridge.poll_video_job(conn, FakeConfig(), job["job_id"])
        conn.close()
        assert result["progress"] == 0.42
