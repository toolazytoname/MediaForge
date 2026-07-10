"""M10 P2 阶段 B：POST /api/v1/contents/{id}/derivative + generate-images
+ derivative_bridge 单元测试。

覆盖：
  - bridge 纯函数：成功 / 错状态 / provider 不可用
  - API 端点：成功 / 404 / 400 / 503 provider_unavailable
  - image_gen.generate_image 全部 mock 掉，不真调 LLM
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from pipeline import db
from pipeline.creators import derivative
from pipeline.creators import image_gen
from pipeline.creators import llm as llm_mod
from pipeline.creators.llm import (
    CompletionResult,
    LLMProvider,
    set_provider,
)
from pipeline.models import (
    Content,
    ContentStatus,
    Topic,
    TopicStatus,
)
from pipeline.sources.dedup import content_hash
from pipeline.utils.errors import BudgetExceeded
from pipeline.webui import deps, derivative_bridge


# ── helpers ────────────────────────────────────────────────


class ScriptedProvider(LLMProvider):
    """模拟 LLM：每次 call 返回预设响应。"""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def call(self, prompt, model, max_tokens):
        self.calls.append({"prompt": prompt, "model": model})
        if not self._responses:
            raise llm_mod.RetryableError("no scripted")
        return CompletionResult(
            text=self._responses.pop(0),
            input_tokens=200, output_tokens=300,
        )


class BudgetProvider(LLMProvider):
    def call(self, prompt, model, max_tokens):
        raise BudgetExceeded(stage="derive", used_usd=100.0, limit_usd=5.0)


def _xhs_payload(n: int = 5) -> dict:
    """构造合规 xiaohongshu 派生 payload。"""
    body_pad = lambda ln: "x" * ln
    slides = [{"type": "cover", "title": "封面", "body": body_pad(40)}]
    for i in range(1, n - 1):
        slides.append({
            "type": "content", "title": f"观点{i}",
            "body": body_pad(min(60, 50 + i * 5)),
        })
    slides.append({"type": "action", "title": "关注", "body": body_pad(20)})
    return {
        "slides": slides,
        "caption": "x" * 100,  # 50-500 字符
        "tags": ["AI", "工具", "效率", "评测", "深度"],
    }


@pytest.fixture(autouse=True)
def reset_provider():
    set_provider(ScriptedProvider([]))
    yield
    set_provider(ScriptedProvider([]))


def _open_db(tmp_path) -> sqlite3.Connection:
    p = tmp_path / "state.db"
    c = db.connect(p)
    db.init_db(c)
    return c


def _seed_topic(conn, *, id="t_top01", title="Topic"):
    t = Topic(
        id=id, source="rss:test", title=title, url=None, summary=None,
        content_hash=content_hash(title, None),
        pillar="ai", score=8.0, score_reason="ok",
        status=TopicStatus.CONSUMED.value,
        created_at="2026-07-05T01:00:00+00:00",
        updated_at="2026-07-05T01:00:00+00:00",
    )
    db.insert_topic(conn, t)
    return t


def _seed_content(
    tmp_path, conn, *, id="c_test01", status=ContentStatus.GATED,
    title="Test",
) -> Content:
    """种 topic + content + canonical.md。返回 Content。"""
    topic_id = f"t_{id[2:]}"
    _seed_topic(conn, id=topic_id, title=title)
    out_dir = tmp_path / "output" / "2026-07-05" / id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "canonical.md").write_text(
        f"# {title}\n\n## 内容\n正文...很长", encoding="utf-8"
    )
    content = Content(
        id=id, topic_id=topic_id, pillar="ai", title=title,
        canonical_path=str(out_dir / "canonical.md"),
        formats=(), gate_score_total=27.0,
        gate_scores={"info": 9, "fun": 9, "view": 9},
        gate_verdict="好",
        status=status,
        created_at="2026-07-05T01:00:00+00:00",
        updated_at="2026-07-05T01:00:00+00:00",
    )
    db.insert_content(conn, content)
    return content


@pytest.fixture
def tmp_env(tmp_path, monkeypatch):
    """临时 state.db + minimal config。"""
    db_path = tmp_path / "state.db"
    c = db.connect(db_path)
    db.init_db(c)
    c.close()
    monkeypatch.setattr(deps, "_DB_PATH", str(db_path))
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "timezone: Asia/Shanghai\n"
        "pillars:\n"
        "  - id: ai\n"
        "    name: AI\n"
        "    description: d\n"
        "    scoring_hint: s\n"
        "sources: []\n"
        "llm: {tiers: {cheap: m, creative: m, critical: m}}\n"
        "budget: {monthly_usd: 80.0}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(deps, "_CONFIG_PATH", str(cfg_path))
    return tmp_path


@pytest.fixture
def client(tmp_env):
    from pipeline.webui.app import create_app
    return TestClient(create_app())


def _fake_generate_image(
    prompt, *, out_path, aspect_ratio="1:1", n=1,
    stage="create_image", ref_id=None, conn=None,
):
    """替代 image_gen.generate_image：写假 PNG 到 out_path + 不写 llm_calls。

    Test 用：避免真调 image provider；cost_usd 由 bridge 自己查 llm_calls
    求和——所以此 mock 不写 llm_calls，bridge 查到的 cost_usd 是 0。
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # 1x1 透明 PNG magic bytes
    out_path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    )
    return image_gen.GeneratedImage(
        bytes_data=b"fake",
        prompt=prompt,
        aspect_ratio=aspect_ratio,
        model="mock-test",
    )


def _fake_generate_image_with_cost(
    prompt, *, out_path, aspect_ratio="1:1", n=1,
    stage="create_image", ref_id=None, conn=None,
):
    """替代 image_gen.generate_image + 写 llm_calls（带 cost）让 bridge 查到非零。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    )
    if conn is not None:
        # 模拟 generate_image 的审计落库
        conn.execute(
            """
            INSERT INTO llm_calls
                (stage, ref_id, model, input_tokens, output_tokens,
                 cost_usd, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (stage, ref_id, "image-01-mock", 100, 1, 0.003, "2026-07-05T06:00:00+00:00"),
        )
        conn.commit()
    return image_gen.GeneratedImage(
        bytes_data=b"fake",
        prompt=prompt,
        aspect_ratio=aspect_ratio,
        model="image-01-mock",
    )


# ── bridge 纯函数：成功路径 ──────────────────────────────────


class TestDeriveXhsBridgeSuccess:
    def test_derive_xhs_success(self, tmp_path, monkeypatch):
        """draft/gated content → derive_xhs → slides.json/caption.md/tags.txt 落盘 +
        Content.formats 含 xiaohongshu。"""
        conn = _open_db(tmp_path)
        c = _seed_content(
            tmp_path, conn, id="c_der_b1", status=ContentStatus.GATED,
        )
        conn.close()

        set_provider(ScriptedProvider([json.dumps(_xhs_payload(5))]))

        conn = db.connect(str(tmp_path / "state.db"))
        try:
            result = derivative_bridge.derive_xhs_for_content(
                conn, "c_der_b1",
                now="2026-07-05T02:00:00+00:00",
            )
        finally:
            conn.close()

        assert result["slides_count"] == 5
        assert result["caption_chars"] == 100
        assert len(result["tags"]) == 5

        # 文件落盘
        out_dir = Path(c.canonical_path).parent
        assert (out_dir / "xiaohongshu" / "slides.json").exists()
        assert (out_dir / "xiaohongshu" / "caption.md").exists()
        assert (out_dir / "xiaohongshu" / "tags.txt").exists()

        # DB formats 含 xiaohongshu
        conn = db.connect(str(tmp_path / "state.db"))
        try:
            row = conn.execute(
                "SELECT formats FROM contents WHERE id=?", ("c_der_b1",)
            ).fetchone()
            formats = json.loads(row["formats"])
            assert "xiaohongshu" in formats
        finally:
            conn.close()

    def test_derive_xhs_works_for_approved(self, tmp_path):
        """approved content 也允许触发（补出图/重出图）。"""
        conn = _open_db(tmp_path)
        _seed_content(
            tmp_path, conn, id="c_der_ap", status=ContentStatus.APPROVED,
        )
        conn.close()

        set_provider(ScriptedProvider([json.dumps(_xhs_payload(5))]))

        conn = db.connect(str(tmp_path / "state.db"))
        try:
            result = derivative_bridge.derive_xhs_for_content(
                conn, "c_der_ap",
                now="2026-07-05T02:00:00+00:00",
            )
        finally:
            conn.close()
        assert result["slides_count"] == 5


class TestDeriveXhsBridgeErrors:
    def test_not_found(self, tmp_path):
        """content 不存在 → ContentNotFoundError。"""
        conn = _open_db(tmp_path)
        try:
            with pytest.raises(derivative_bridge.ContentNotFoundError) as ei:
                derivative_bridge.derive_xhs_for_content(
                    conn, "c_nope01",
                    now="2026-07-05T02:00:00+00:00",
                )
            assert "c_nope01" in str(ei.value)
        finally:
            conn.close()

    def test_wrong_status(self, tmp_path):
        """status=done → ContentStatusError（已发出去别再改）。"""
        conn = _open_db(tmp_path)
        _seed_content(
            tmp_path, conn, id="c_done01", status=ContentStatus.DONE,
        )
        conn.close()

        conn = db.connect(str(tmp_path / "state.db"))
        try:
            with pytest.raises(derivative_bridge.ContentStatusError) as ei:
                derivative_bridge.derive_xhs_for_content(
                    conn, "c_done01",
                    now="2026-07-05T02:00:00+00:00",
                )
            assert "done" in str(ei.value)
            assert "c_done01" in str(ei.value)
        finally:
            conn.close()

    def test_wrong_status_published(self, tmp_path):
        """status=published 不在白名单 → ContentStatusError。"""
        conn = _open_db(tmp_path)
        _seed_content(
            tmp_path, conn, id="c_pub01", status=ContentStatus.DISCARDED,
        )
        conn.close()

        conn = db.connect(str(tmp_path / "state.db"))
        try:
            with pytest.raises(derivative_bridge.ContentStatusError):
                derivative_bridge.derive_xhs_for_content(
                    conn, "c_pub01",
                    now="2026-07-05T02:00:00+00:00",
                )
        finally:
            conn.close()

    def test_budget_exceeded_propagates(self, tmp_path):
        """BudgetExceeded 原样上抛（系统级，编排层终止整批）。"""
        conn = _open_db(tmp_path)
        _seed_content(
            tmp_path, conn, id="c_bud_b1", status=ContentStatus.GATED,
        )
        conn.close()

        set_provider(BudgetProvider())

        conn = db.connect(str(tmp_path / "state.db"))
        try:
            with pytest.raises(BudgetExceeded):
                derivative_bridge.derive_xhs_for_content(
                    conn, "c_bud_b1",
                    now="2026-07-05T02:00:00+00:00",
                )
        finally:
            conn.close()


# ── bridge 纯函数：generate_images ──────────────────────────


class TestGenerateImagesBridgeSuccess:
    def test_generate_images_success(self, tmp_path, monkeypatch):
        """mock generate_image → cover.png + inline-*.png 落盘 +
        Content.cover_path/inline_images 更新。"""
        conn = _open_db(tmp_path)
        c = _seed_content(
            tmp_path, conn, id="c_img_b1", status=ContentStatus.APPROVED,
        )
        conn.close()

        monkeypatch.setattr(
            image_gen, "generate_image", _fake_generate_image_with_cost,
        )

        conn = db.connect(str(tmp_path / "state.db"))
        try:
            result = derivative_bridge.generate_images_for_content(
                conn, "c_img_b1",
                aspect_ratio="3:4",
                now="2026-07-05T02:00:00+00:00",
            )
        finally:
            conn.close()

        # 1 cover + 5 inline = 6 files
        out_dir = Path(c.canonical_path).parent
        assert (out_dir / "cover.png").exists()
        for i in range(1, 6):
            assert (out_dir / "images" / f"inline-{i}.png").exists()

        # 返回结构
        assert result["cover_path"].endswith("cover.png")
        assert len(result["inline_images"]) == 5
        for i, p in enumerate(result["inline_images"], 1):
            assert p.endswith(f"inline-{i}.png")
        # cost_usd 由 mock 写 llm_calls 6 条 × 0.003 = 0.018
        assert result["cost_usd"] == pytest.approx(0.018, abs=1e-6)

        # DB 落库
        conn = db.connect(str(tmp_path / "state.db"))
        try:
            row = conn.execute(
                "SELECT cover_path, inline_images FROM contents "
                "WHERE id=?", ("c_img_b1",),
            ).fetchone()
            assert row["cover_path"].endswith("cover.png")
            inlines = json.loads(row["inline_images"])
            assert len(inlines) == 5
            assert all(p.endswith(".png") for p in inlines)
        finally:
            conn.close()

    def test_generate_images_uses_slide_count_when_xhs_exists(
        self, tmp_path, monkeypatch,
    ):
        """有 xhs/slides.json 时，inline 数量 = slide 数。"""
        conn = _open_db(tmp_path)
        c = _seed_content(
            tmp_path, conn, id="c_img_x1", status=ContentStatus.APPROVED,
        )
        # 写一个 3 张的 slides.json
        xhs_dir = Path(c.canonical_path).parent / "xiaohongshu"
        xhs_dir.mkdir(parents=True, exist_ok=True)
        (xhs_dir / "slides.json").write_text(json.dumps([
            {"type": "cover", "title": "封面", "body": "b1"},
            {"type": "content", "title": "中", "body": "b2"},
            {"type": "action", "title": "行", "body": "b3"},
        ]), encoding="utf-8")
        conn.close()

        monkeypatch.setattr(
            image_gen, "generate_image", _fake_generate_image,
        )

        conn = db.connect(str(tmp_path / "state.db"))
        try:
            result = derivative_bridge.generate_images_for_content(
                conn, "c_img_x1", aspect_ratio="3:4",
                now="2026-07-05T02:00:00+00:00",
            )
        finally:
            conn.close()

        # 3 张 slide → 3 inline
        assert len(result["inline_images"]) == 3
        out_dir = Path(c.canonical_path).parent
        for i in range(1, 4):
            assert (out_dir / "images" / f"inline-{i}.png").exists()
        # 不应生成 inline-4/5
        assert not (out_dir / "images" / "inline-4.png").exists()


class TestGenerateImagesBridgeErrors:
    def test_not_found(self, tmp_path, monkeypatch):
        """content 不存在 → ContentNotFoundError。"""
        conn = _open_db(tmp_path)
        try:
            with pytest.raises(derivative_bridge.ContentNotFoundError):
                derivative_bridge.generate_images_for_content(
                    conn, "c_nope_img",
                    now="2026-07-05T02:00:00+00:00",
                )
        finally:
            conn.close()

    def test_wrong_status(self, tmp_path, monkeypatch):
        """status=published 不在白名单 → ContentStatusError。"""
        conn = _open_db(tmp_path)
        _seed_content(
            tmp_path, conn, id="c_img_ws", status=ContentStatus.DONE,
        )
        conn.close()

        conn = db.connect(str(tmp_path / "state.db"))
        try:
            with pytest.raises(derivative_bridge.ContentStatusError):
                derivative_bridge.generate_images_for_content(
                    conn, "c_img_ws",
                    now="2026-07-05T02:00:00+00:00",
                )
        finally:
            conn.close()

    def test_provider_unavailable_retryable(self, tmp_path, monkeypatch):
        """generate_image 抛 RetryableError → ImageProviderError（重试耗尽）。"""
        conn = _open_db(tmp_path)
        _seed_content(
            tmp_path, conn, id="c_img_re", status=ContentStatus.APPROVED,
        )
        conn.close()

        def raise_retry(*args, **kwargs):
            raise image_gen.RetryableError("mocked 5xx exhausted")

        monkeypatch.setattr(image_gen, "generate_image", raise_retry)

        conn = db.connect(str(tmp_path / "state.db"))
        try:
            with pytest.raises(derivative_bridge.ImageProviderError) as ei:
                derivative_bridge.generate_images_for_content(
                    conn, "c_img_re", aspect_ratio="3:4",
                    now="2026-07-05T02:00:00+00:00",
                )
            assert "retry exhausted" in str(ei.value)
        finally:
            conn.close()

    def test_provider_unavailable_value_error(self, tmp_path, monkeypatch):
        """generate_image 抛 ValueError → ImageProviderError（4xx/响应残缺）。"""
        conn = _open_db(tmp_path)
        _seed_content(
            tmp_path, conn, id="c_img_ve", status=ContentStatus.APPROVED,
        )
        conn.close()

        def raise_ve(*args, **kwargs):
            raise ValueError("HTTP 400: bad prompt")

        monkeypatch.setattr(image_gen, "generate_image", raise_ve)

        conn = db.connect(str(tmp_path / "state.db"))
        try:
            with pytest.raises(derivative_bridge.ImageProviderError) as ei:
                derivative_bridge.generate_images_for_content(
                    conn, "c_img_ve", aspect_ratio="3:4",
                    now="2026-07-05T02:00:00+00:00",
                )
            assert "HTTP 400" in str(ei.value)
        finally:
            conn.close()

    def test_budget_exceeded_propagates(self, tmp_path, monkeypatch):
        """BudgetExceeded 原样上抛（不被 bridge 吞）。"""
        conn = _open_db(tmp_path)
        _seed_content(
            tmp_path, conn, id="c_img_bud", status=ContentStatus.APPROVED,
        )
        conn.close()

        def raise_budget(*args, **kwargs):
            raise BudgetExceeded(
                stage="create_cover", used_usd=100.0, limit_usd=5.0,
            )

        monkeypatch.setattr(image_gen, "generate_image", raise_budget)

        conn = db.connect(str(tmp_path / "state.db"))
        try:
            with pytest.raises(BudgetExceeded):
                derivative_bridge.generate_images_for_content(
                    conn, "c_img_bud", aspect_ratio="3:4",
                    now="2026-07-05T02:00:00+00:00",
                )
        finally:
            conn.close()


# ── API 端点：POST /api/v1/contents/{id}/derivative ─────────


class TestPostDerivativeEndpoint:
    def test_200_with_derivative_dict(self, client, tmp_env, monkeypatch):
        """成功 → 200 + {derivative: {slides_count, caption_chars, tags}}。"""
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_content(
            tmp_env, conn, id="c_api_d1", status=ContentStatus.GATED,
        )
        conn.close()

        set_provider(ScriptedProvider([json.dumps(_xhs_payload(5))]))

        r = client.post("/api/v1/contents/c_api_d1/derivative")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "derivative" in body
        d = body["derivative"]
        assert d["slides_count"] == 5
        assert d["caption_chars"] == 100
        assert len(d["tags"]) == 5

    def test_404_content_not_found(self, client):
        """不存在 id → 404 envelope。"""
        r = client.post("/api/v1/contents/c_nope_api/derivative")
        assert r.status_code == 404
        body = r.json()
        assert body["detail"]["error"]["code"] == "content_not_found"
        assert "c_nope_api" in body["detail"]["error"]["message"]

    def test_400_wrong_status(self, client, tmp_env):
        """status=done → 400 envelope（不消费 done content）。"""
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_content(
            tmp_env, conn, id="c_api_d2", status=ContentStatus.DONE,
        )
        conn.close()

        r = client.post("/api/v1/contents/c_api_d2/derivative")
        assert r.status_code == 400
        body = r.json()
        assert body["detail"]["error"]["code"] == "wrong_status"

    def test_503_budget_exceeded(self, client, tmp_env):
        """BudgetExceeded → 503 envelope。"""
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_content(
            tmp_env, conn, id="c_api_d3", status=ContentStatus.GATED,
        )
        conn.close()

        set_provider(BudgetProvider())

        r = client.post("/api/v1/contents/c_api_d3/derivative")
        assert r.status_code == 503
        body = r.json()
        assert body["detail"]["error"]["code"] == "budget_exceeded"


# ── API 端点：POST /api/v1/contents/{id}/generate-images ─────


class TestPostGenerateImagesEndpoint:
    def test_200_with_cover_inline_cost(
        self, client, tmp_env, monkeypatch,
    ):
        """成功 → 200 + {cover_path, inline_images, cost_usd}。"""
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_content(
            tmp_env, conn, id="c_api_g1", status=ContentStatus.APPROVED,
        )
        conn.close()

        monkeypatch.setattr(
            image_gen, "generate_image", _fake_generate_image_with_cost,
        )

        r = client.post("/api/v1/contents/c_api_g1/generate-images")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["cover_path"].endswith("cover.png")
        assert len(body["inline_images"]) == 5
        assert all(p.endswith(".png") for p in body["inline_images"])
        assert body["cost_usd"] == pytest.approx(0.018, abs=1e-6)

    def test_404_content_not_found(self, client):
        """不存在 id → 404 envelope。"""
        r = client.post("/api/v1/contents/c_nope_img_api/generate-images")
        assert r.status_code == 404
        body = r.json()
        assert body["detail"]["error"]["code"] == "content_not_found"

    def test_400_wrong_status(self, client, tmp_env):
        """status=done → 400 envelope。"""
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_content(
            tmp_env, conn, id="c_api_g2", status=ContentStatus.DONE,
        )
        conn.close()

        r = client.post("/api/v1/contents/c_api_g2/generate-images")
        assert r.status_code == 400
        body = r.json()
        assert body["detail"]["error"]["code"] == "wrong_status"

    def test_503_provider_unavailable(
        self, client, tmp_env, monkeypatch,
    ):
        """provider 抛 RetryableError → 503 envelope code=image_provider_unavailable。"""
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_content(
            tmp_env, conn, id="c_api_g3", status=ContentStatus.APPROVED,
        )
        conn.close()

        def raise_retry(*args, **kwargs):
            raise image_gen.RetryableError("mocked retry exhausted")

        monkeypatch.setattr(image_gen, "generate_image", raise_retry)

        r = client.post("/api/v1/contents/c_api_g3/generate-images")
        assert r.status_code == 503
        body = r.json()
        assert body["detail"]["error"]["code"] == "image_provider_unavailable"
        assert "retry exhausted" in body["detail"]["error"]["message"]
