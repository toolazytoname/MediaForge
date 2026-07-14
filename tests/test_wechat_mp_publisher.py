"""M13 公众号 wechat_mp Publisher 测试（结构镜像 tests/test_x_publisher.py）。

覆盖：
  - TestCredentialLoad：secrets/wechat_mp_<account>.json 读取
  - TestTokenCaching：access_token 内存缓存 + 到期刷新 + dry_run 从不触网
  - TestValidate：本地校验（不触网络）
  - TestPublishDraft：dry_run / 真实两步流程（封面 → 草稿）
  - TestErrorClassification：errcode → LoginExpired / PublishError 分类
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.publishers.base import (
    AccountConfig,
    LoginExpired,
    PostBundle,
    PublishError,
    PublisherAdapter,
)


# ── 公共 helper ───────────────────────────────────────────

def _bundle(content_dir: Path, content_id: str = "c_wm") -> PostBundle:
    return PostBundle(
        content_id=content_id, title="t",
        body_path=content_dir / "canonical.md",
        media_paths=(), tags=(), extra={},
    )


def _account() -> AccountConfig:
    return AccountConfig(
        id="main",
        credentials_path=Path("secrets/wechat_mp_main.json"),
    )


def _seed_valid_bundle(content_dir: Path) -> None:
    """写一份完整合法的 wechat_mp 产物 + 封面。"""
    wechat_dir = content_dir / "wechat_mp"
    wechat_dir.mkdir(parents=True, exist_ok=True)
    (wechat_dir / "meta.json").write_text(
        json.dumps({"title": "标题", "digest": "摘要"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (wechat_dir / "article.md").write_text("# 标题\n\n正文……", encoding="utf-8")
    (content_dir / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 100)


def _seed_bundle_with_inline_image(content_dir: Path, *, write_image_file: bool = True) -> None:
    """写一份含正文内嵌真实插图（拼接产物，见 derivative_wechat_mp.insert_generated_images）
    的 wechat_mp 产物 + 封面。"""
    wechat_dir = content_dir / "wechat_mp"
    wechat_dir.mkdir(parents=True, exist_ok=True)
    (wechat_dir / "meta.json").write_text(
        json.dumps({"title": "标题", "digest": "摘要"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (wechat_dir / "article.md").write_text(
        "# 标题\n\n## 第一部分\n\n正文……\n\n![一张示意图](../images/inline-1.png)\n\n后续正文……",
        encoding="utf-8",
    )
    (content_dir / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 100)
    if write_image_file:
        images_dir = content_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        (images_dir / "inline-1.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"y" * 50)


# ── TestCredentialLoad ────────────────────────────────────

class TestCredentialLoad:
    def test_load_valid_credentials(self, tmp_path: Path) -> None:
        creds = tmp_path / "wechat_mp_main.json"
        creds.write_text(json.dumps({"app_id": "wx123", "app_secret": "secret123"}))

        from pipeline.publishers.wechat_mp import load_wechat_credentials
        app_id, app_secret = load_wechat_credentials(creds)
        assert app_id == "wx123"
        assert app_secret == "secret123"

    def test_load_missing_file_raises(self, tmp_path: Path) -> None:
        from pipeline.publishers.wechat_mp import load_wechat_credentials
        with pytest.raises(FileNotFoundError):
            load_wechat_credentials(tmp_path / "nonexistent.json")

    def test_load_missing_app_id_raises(self, tmp_path: Path) -> None:
        creds = tmp_path / "wechat_mp_main.json"
        creds.write_text(json.dumps({"app_secret": "secret123"}))
        from pipeline.publishers.wechat_mp import load_wechat_credentials
        with pytest.raises(ValueError, match="app_id"):
            load_wechat_credentials(creds)

    def test_load_missing_app_secret_raises(self, tmp_path: Path) -> None:
        creds = tmp_path / "wechat_mp_main.json"
        creds.write_text(json.dumps({"app_id": "wx123"}))
        from pipeline.publishers.wechat_mp import load_wechat_credentials
        with pytest.raises(ValueError, match="app_secret"):
            load_wechat_credentials(creds)


# ── TestTokenCaching ───────────────────────────────────────

class TestTokenCaching:
    def _make_adapter(self, *, get_responses: list[dict]):
        from pipeline.publishers.wechat_mp import WechatMpPublisher
        calls: list[dict] = []

        def fake_get(url, *, params, timeout=30.0):
            calls.append({"url": url, "params": params})
            idx = len(calls) - 1
            if idx >= len(get_responses):
                raise AssertionError("ran out of mock token responses")
            return get_responses[idx]

        adapter = WechatMpPublisher(
            app_id="wx1", app_secret="s1", http_get=fake_get,
            http_post=lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not post")),
            http_upload=lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not upload")),
        )
        return adapter, calls

    def test_first_call_fetches_token(self) -> None:
        adapter, calls = self._make_adapter(
            get_responses=[{"access_token": "tok1", "expires_in": 7200}],
        )
        token = adapter._ensure_access_token()
        assert token == "tok1"
        assert len(calls) == 1

    def test_second_call_reuses_cached_token(self) -> None:
        adapter, calls = self._make_adapter(
            get_responses=[{"access_token": "tok1", "expires_in": 7200}],
        )
        adapter._ensure_access_token()
        adapter._ensure_access_token()
        assert len(calls) == 1  # 第二次复用缓存，未再拉取

    def test_expired_token_refetches(self) -> None:
        adapter, calls = self._make_adapter(
            get_responses=[
                {"access_token": "tok1", "expires_in": 7200},
                {"access_token": "tok2", "expires_in": 7200},
            ],
        )
        adapter._ensure_access_token()
        # 手动让 token 过期（跳过等待真实 7200s）
        adapter._token_expires_at = 0.0
        token = adapter._ensure_access_token()
        assert token == "tok2"
        assert len(calls) == 2

    def test_missing_access_token_raises(self) -> None:
        adapter, _ = self._make_adapter(get_responses=[{"errmsg": "weird"}])
        with pytest.raises(PublishError, match="access_token"):
            adapter._ensure_access_token()

    def test_dry_run_never_calls_token(self, tmp_path: Path) -> None:
        from pipeline.publishers.wechat_mp import WechatMpPublisher

        content_dir = tmp_path / "content"
        _seed_valid_bundle(content_dir)

        def must_not_call(*a, **k):
            raise AssertionError("dry-run must not fetch token / call http")

        adapter = WechatMpPublisher(
            app_id="wx1", app_secret="s1",
            http_get=must_not_call, http_post=must_not_call, http_upload=must_not_call,
        )
        result = adapter.publish(_bundle(content_dir), _account(), dry_run=True)
        assert (result.platform_post_id or "").startswith("dry-")


# ── TestValidate（不触网络） ──────────────────────────────

class TestValidate:
    def _make_adapter(self) -> PublisherAdapter:
        from pipeline.publishers.wechat_mp import WechatMpPublisher
        return WechatMpPublisher(
            app_id="wx1", app_secret="s1",
            http_get=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network")),
            http_post=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network")),
            http_upload=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network")),
        )

    def test_validate_happy_path(self, tmp_path: Path) -> None:
        content_dir = tmp_path / "content"
        _seed_valid_bundle(content_dir)
        a = self._make_adapter()
        assert a.validate(_bundle(content_dir)) == []

    def test_validate_missing_meta_json(self, tmp_path: Path) -> None:
        content_dir = tmp_path / "content"
        _seed_valid_bundle(content_dir)
        (content_dir / "wechat_mp" / "meta.json").unlink()
        a = self._make_adapter()
        issues = a.validate(_bundle(content_dir))
        assert any("meta.json" in i for i in issues)

    def test_validate_missing_article(self, tmp_path: Path) -> None:
        content_dir = tmp_path / "content"
        _seed_valid_bundle(content_dir)
        (content_dir / "wechat_mp" / "article.md").unlink()
        a = self._make_adapter()
        issues = a.validate(_bundle(content_dir))
        assert any("article.md" in i for i in issues)

    def test_validate_missing_cover(self, tmp_path: Path) -> None:
        content_dir = tmp_path / "content"
        _seed_valid_bundle(content_dir)
        (content_dir / "cover.png").unlink()
        a = self._make_adapter()
        issues = a.validate(_bundle(content_dir))
        assert any("cover" in i.lower() for i in issues)

    def test_validate_title_too_long(self, tmp_path: Path) -> None:
        content_dir = tmp_path / "content"
        _seed_valid_bundle(content_dir)
        (content_dir / "wechat_mp" / "meta.json").write_text(
            json.dumps({"title": "x" * 65, "digest": "摘要"}), encoding="utf-8",
        )
        a = self._make_adapter()
        issues = a.validate(_bundle(content_dir))
        assert any("title too long" in i for i in issues)

    def test_validate_digest_too_long(self, tmp_path: Path) -> None:
        content_dir = tmp_path / "content"
        _seed_valid_bundle(content_dir)
        (content_dir / "wechat_mp" / "meta.json").write_text(
            json.dumps({"title": "标题", "digest": "x" * 121}), encoding="utf-8",
        )
        a = self._make_adapter()
        issues = a.validate(_bundle(content_dir))
        assert any("digest too long" in i for i in issues)

    def test_validate_empty_article(self, tmp_path: Path) -> None:
        content_dir = tmp_path / "content"
        _seed_valid_bundle(content_dir)
        (content_dir / "wechat_mp" / "article.md").write_text("", encoding="utf-8")
        a = self._make_adapter()
        issues = a.validate(_bundle(content_dir))
        assert any("empty" in i.lower() for i in issues)

    def test_validate_cover_too_large(self, tmp_path: Path) -> None:
        from pipeline.publishers.wechat_mp import COVER_MAX_BYTES
        content_dir = tmp_path / "content"
        _seed_valid_bundle(content_dir)
        (content_dir / "cover.png").write_bytes(b"x" * (COVER_MAX_BYTES + 1))
        a = self._make_adapter()
        issues = a.validate(_bundle(content_dir))
        assert any("too large" in i for i in issues)

    def test_validate_missing_inline_image_file(self, tmp_path: Path) -> None:
        content_dir = tmp_path / "content"
        _seed_bundle_with_inline_image(content_dir, write_image_file=False)
        a = self._make_adapter()
        issues = a.validate(_bundle(content_dir))
        assert any("inline-1.png" in i for i in issues)

    def test_validate_happy_path_with_inline_image_present(self, tmp_path: Path) -> None:
        content_dir = tmp_path / "content"
        _seed_bundle_with_inline_image(content_dir, write_image_file=True)
        a = self._make_adapter()
        assert a.validate(_bundle(content_dir)) == []


# ── TestPublishDraft ───────────────────────────────────────

class TestPublishDraft:
    def _make_adapter(self, *, upload_resp=None, post_resp=None):
        from pipeline.publishers.wechat_mp import WechatMpPublisher
        calls: list[dict] = []

        def fake_get(url, *, params, timeout=30.0):
            calls.append({"kind": "get", "url": url, "params": params})
            return {"access_token": "tok1", "expires_in": 7200}

        def fake_upload(url, *, params, file_path, timeout=60.0):
            calls.append({"kind": "upload", "url": url, "params": params, "file_path": file_path})
            return upload_resp if upload_resp is not None else {"media_id": "cover_media_1"}

        def fake_post(url, *, params, json_body, timeout=30.0):
            calls.append({"kind": "post", "url": url, "params": params, "json_body": json_body})
            return post_resp if post_resp is not None else {"media_id": "draft_media_1"}

        adapter = WechatMpPublisher(
            app_id="wx1", app_secret="s1",
            http_get=fake_get, http_post=fake_post, http_upload=fake_upload,
        )
        return adapter, calls

    def test_dry_run_does_not_call_http(self, tmp_path: Path) -> None:
        content_dir = tmp_path / "content"
        _seed_valid_bundle(content_dir)
        adapter, calls = self._make_adapter()
        result = adapter.publish(_bundle(content_dir), _account(), dry_run=True)
        assert calls == []
        assert (result.platform_post_id or "").startswith("dry-draft-")

    def test_real_publish_uploads_cover_then_draft(self, tmp_path: Path) -> None:
        content_dir = tmp_path / "content"
        _seed_valid_bundle(content_dir)
        adapter, calls = self._make_adapter()
        result = adapter.publish(_bundle(content_dir), _account(), dry_run=False)

        kinds = [c["kind"] for c in calls]
        assert kinds == ["get", "upload", "post"]
        # thumb_media_id 从封面上传结果正确传入草稿 JSON body
        draft_call = calls[2]
        assert draft_call["json_body"]["articles"][0]["thumb_media_id"] == "cover_media_1"
        assert result.platform_post_id == "draft_media_1"
        assert result.url is None  # 草稿箱无公开 url

    def test_cover_upload_failure_stops_before_draft(self, tmp_path: Path) -> None:
        content_dir = tmp_path / "content"
        _seed_valid_bundle(content_dir)
        adapter, calls = self._make_adapter(upload_resp={"media_id": ""})
        with pytest.raises(PublishError, match="cover upload"):
            adapter.publish(_bundle(content_dir), _account(), dry_run=False)
        kinds = [c["kind"] for c in calls]
        assert "post" not in kinds  # 封面失败，草稿步骤不应被调用

    def test_draft_missing_media_id_raises(self, tmp_path: Path) -> None:
        content_dir = tmp_path / "content"
        _seed_valid_bundle(content_dir)
        adapter, _ = self._make_adapter(post_resp={"errmsg": "weird"})
        with pytest.raises(PublishError, match="media_id"):
            adapter.publish(_bundle(content_dir), _account(), dry_run=False)

    def test_second_publish_reuses_cached_token(self, tmp_path: Path) -> None:
        content_dir = tmp_path / "content"
        _seed_valid_bundle(content_dir)
        adapter, calls = self._make_adapter()
        adapter.publish(_bundle(content_dir), _account(), dry_run=False)
        adapter.publish(_bundle(content_dir), _account(), dry_run=False)
        get_calls = [c for c in calls if c["kind"] == "get"]
        assert len(get_calls) == 1  # 第二次发布复用缓存 token


# ── TestPublishInlineImages（正文内嵌真实插图 → 微信 CDN）───

class TestPublishInlineImages:
    def _make_adapter(self, *, content_image_url: str = "https://mmbiz.qpic.cn/fake/1"):
        from pipeline.publishers.wechat_mp import WechatMpPublisher
        calls: list[dict] = []

        def fake_get(url, *, params, timeout=30.0):
            calls.append({"kind": "get", "url": url, "params": params})
            return {"access_token": "tok1", "expires_in": 7200}

        def fake_upload(url, *, params, file_path, timeout=60.0):
            calls.append({"kind": "upload", "url": url, "params": params, "file_path": file_path})
            if url.endswith("/media/uploadimg"):
                return {"url": content_image_url}
            return {"media_id": "cover_media_1"}

        def fake_post(url, *, params, json_body, timeout=30.0):
            calls.append({"kind": "post", "url": url, "params": params, "json_body": json_body})
            return {"media_id": "draft_media_1"}

        adapter = WechatMpPublisher(
            app_id="wx1", app_secret="s1",
            http_get=fake_get, http_post=fake_post, http_upload=fake_upload,
        )
        return adapter, calls

    def test_real_publish_uploads_inline_image_and_rewrites_url(self, tmp_path: Path) -> None:
        content_dir = tmp_path / "content"
        _seed_bundle_with_inline_image(content_dir)
        adapter, calls = self._make_adapter(content_image_url="https://mmbiz.qpic.cn/abc123")

        adapter.publish(_bundle(content_dir), _account(), dry_run=False)

        upload_calls = [c for c in calls if c["kind"] == "upload"]
        content_image_calls = [c for c in upload_calls if c["url"].endswith("/media/uploadimg")]
        assert len(content_image_calls) == 1
        assert content_image_calls[0]["file_path"] == content_dir / "images" / "inline-1.png"

        draft_call = next(c for c in calls if c["kind"] == "post")
        html = draft_call["json_body"]["articles"][0]["content"]
        assert "https://mmbiz.qpic.cn/abc123" in html
        assert "../images/inline-1.png" not in html

    def test_dry_run_does_not_upload_inline_images(self, tmp_path: Path) -> None:
        content_dir = tmp_path / "content"
        _seed_bundle_with_inline_image(content_dir)
        adapter, calls = self._make_adapter()
        adapter.publish(_bundle(content_dir), _account(), dry_run=True)
        assert calls == []

    def test_publish_raises_when_inline_image_file_missing(self, tmp_path: Path) -> None:
        content_dir = tmp_path / "content"
        _seed_bundle_with_inline_image(content_dir, write_image_file=False)
        adapter, calls = self._make_adapter()
        with pytest.raises(PublishError, match="inline image"):
            adapter.publish(_bundle(content_dir), _account(), dry_run=False)
        assert not any(c["kind"] == "post" for c in calls)

    def test_content_image_upload_missing_url_raises(self, tmp_path: Path) -> None:
        content_dir = tmp_path / "content"
        _seed_bundle_with_inline_image(content_dir)
        adapter, _ = self._make_adapter()

        def bad_upload(url, *, params, file_path, timeout=60.0):
            return {"errmsg": "weird"}

        adapter._upload = bad_upload
        with pytest.raises(PublishError, match="url"):
            adapter.publish(_bundle(content_dir), _account(), dry_run=False)

    def test_article_without_inline_images_unaffected(self, tmp_path: Path) -> None:
        """回归：无插图正文的既有行为不变（不多触发任何 upload/post 调用）。"""
        content_dir = tmp_path / "content"
        _seed_valid_bundle(content_dir)
        adapter, calls = self._make_adapter()
        adapter.publish(_bundle(content_dir), _account(), dry_run=False)
        kinds = [c["kind"] for c in calls]
        assert kinds == ["get", "upload", "post"]


# ── TestErrorClassification ───────────────────────────────

class TestErrorClassification:
    def test_40001_raises_login_expired(self) -> None:
        from pipeline.publishers.wechat_mp import _classify_wechat_error
        err = _classify_wechat_error(40001, "invalid credential")
        assert isinstance(err, LoginExpired)

    def test_40164_includes_ip_whitelist_hint(self) -> None:
        from pipeline.publishers.wechat_mp import _classify_wechat_error
        err = _classify_wechat_error(40164, "invalid ip")
        assert isinstance(err, LoginExpired)
        assert "白名单" in str(err)

    def test_48001_includes_permission_hint(self) -> None:
        from pipeline.publishers.wechat_mp import _classify_wechat_error
        err = _classify_wechat_error(48001, "api unauthorized")
        assert isinstance(err, LoginExpired)
        assert "权限" in str(err) or "认证" in str(err)

    def test_45009_rate_limit_error(self) -> None:
        from pipeline.publishers.wechat_mp import _classify_wechat_error
        err = _classify_wechat_error(45009, "reach max api daily quota")
        assert isinstance(err, PublishError)
        assert not isinstance(err, LoginExpired)
        assert "rate_limit" in str(err)

    def test_45008_quota_error(self) -> None:
        from pipeline.publishers.wechat_mp import _classify_wechat_error
        err = _classify_wechat_error(45008, "quota exceeded")
        assert isinstance(err, PublishError)
        assert not isinstance(err, LoginExpired)
        assert "quota" in str(err)

    def test_unknown_errcode_generic_publish_error(self) -> None:
        from pipeline.publishers.wechat_mp import _classify_wechat_error
        err = _classify_wechat_error(99999, "unknown thing")
        assert isinstance(err, PublishError)
        assert not isinstance(err, LoginExpired)

    def test_http_5xx_raises_publish_error_not_login_expired(self) -> None:
        from pipeline.publishers.wechat_mp import _parse_json_response
        from unittest.mock import MagicMock

        fake_resp = MagicMock()
        fake_resp.status_code = 503
        fake_resp.text = "Service Unavailable"
        with pytest.raises(PublishError) as exc_info:
            _parse_json_response(fake_resp, "https://api.weixin.qq.com/cgi-bin/token")
        assert not isinstance(exc_info.value, LoginExpired)
        assert "503" in str(exc_info.value)
