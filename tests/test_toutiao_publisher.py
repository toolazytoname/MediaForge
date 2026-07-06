"""M4-3 头条 Publisher 单元测试（TDD — RED 阶段先写，GREEN 后跑通）。

测试契约（HARD_PARTS §2）：
- platform = 'toutiao'
- 防腐层：选择器集中在 toutiao_selectors.py（HARD_PARTS §2 决策 4）
- cookie 失效检测先行（HARD_PARTS §2 决策 2）
- dry_run 不调浏览器
- Playwright 调用通过注入（health_probe / publish_fn），CI 无 playwright 也能跑
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pipeline.publishers.base import (
    AccountConfig,
    LoginExpired,
    PostBundle,
    PublishError,
    PublishResult,
)
from pipeline.publishers.toutiao import (
    BODY_MAX_LEN,
    BODY_MIN_LEN,
    TITLE_MAX_LEN,
    ToutiaoPublisher,
)


# ── fixtures ───────────────────────────────────────────────


@pytest.fixture
def cookies_path(tmp_path: Path) -> Path:
    """合法 Playwright storage_state JSON（最小可用）。"""
    p = tmp_path / "toutiao_main.json"
    p.write_text(json.dumps({
        "cookies": [
            {
                "name": "sessionid",
                "value": "fake-session",
                "domain": ".toutiao.com",
                "path": "/",
                "expires": 9999999999,
                "httpOnly": True,
                "secure": True,
                "sameSite": "None",
            },
        ],
        "origins": [],
    }))
    return p


@pytest.fixture
def account() -> AccountConfig:
    return AccountConfig(
        id="main",
        credentials_path=Path("secrets/cookies/toutiao_main.json"),
    )


@pytest.fixture
def out_root(tmp_path: Path) -> Path:
    """模拟 <content_dir>/{canonical.md, toutiao.md}。"""
    d = tmp_path / "c_tt001"
    d.mkdir(parents=True)
    # canonical.md 写满足够字数（> 300 字符）
    (d / "canonical.md").write_text(
        "OpenAI 发布了一个新模型。\n\n" + ("正文内容" * 100),
        encoding="utf-8",
    )
    (d / "toutiao.md").write_text(
        "OpenAI 发布了一个新模型。\n\n" + ("正文内容" * 100),
        encoding="utf-8",
    )
    return d


def _bundle(out_root: Path, title: str = "Test Toutiao Publish") -> PostBundle:
    return PostBundle(
        content_id="c_tt001",
        title=title,
        body_path=out_root / "toutiao.md",
        media_paths=(),
        tags=(),
        extra={"platform": "toutiao"},
    )


# ── 构造 ────────────────────────────────────────────────────


def test_init_requires_cookies_path() -> None:
    with pytest.raises(ValueError, match="cookies_path"):
        ToutiaoPublisher(cookies_path=None)  # type: ignore[arg-type]


# ── validate ────────────────────────────────────────────────


def test_validate_empty_title(tmp_path: Path) -> None:
    pub = ToutiaoPublisher(cookies_path=tmp_path / "x.json")
    issues = pub.validate(_bundle(out_root := tmp_path, title=""))
    assert any("empty" in i.lower() for i in issues)


def test_validate_title_too_long(tmp_path: Path) -> None:
    pub = ToutiaoPublisher(cookies_path=tmp_path / "x.json")
    long_title = "x" * (TITLE_MAX_LEN + 1)
    issues = pub.validate(_bundle(out_root := tmp_path, title=long_title))
    assert any(f"max {TITLE_MAX_LEN}" in i for i in issues)


def test_validate_body_too_short(tmp_path: Path) -> None:
    d = tmp_path / "c_short"
    d.mkdir(parents=True)
    (d / "toutiao.md").write_text("很短", encoding="utf-8")
    pub = ToutiaoPublisher(cookies_path=tmp_path / "x.json")
    issues = pub.validate(_bundle(d))
    assert any(f"min {BODY_MIN_LEN}" in i for i in issues)


def test_validate_body_too_long(tmp_path: Path) -> None:
    d = tmp_path / "c_long"
    d.mkdir(parents=True)
    (d / "toutiao.md").write_text("字" * (BODY_MAX_LEN + 100), encoding="utf-8")
    pub = ToutiaoPublisher(cookies_path=tmp_path / "x.json")
    issues = pub.validate(_bundle(d))
    assert any(f"max {BODY_MAX_LEN}" in i for i in issues)


def test_validate_body_file_missing(tmp_path: Path) -> None:
    pub = ToutiaoPublisher(cookies_path=tmp_path / "x.json")
    # body_path 不存在且无 toutiao.md fallback
    issues = pub.validate(_bundle(tmp_path, title="ok"))
    assert any("not found" in i for i in issues)


def test_validate_cookies_missing(tmp_path: Path) -> None:
    d = tmp_path / "c_x"
    d.mkdir(parents=True)
    (d / "toutiao.md").write_text("body " * 100, encoding="utf-8")
    pub = ToutiaoPublisher(cookies_path=tmp_path / "no_such_file.json")
    issues = pub.validate(_bundle(d))
    assert any("cookies file missing" in i for i in issues)


def test_validate_all_good(out_root: Path, cookies_path: Path) -> None:
    pub = ToutiaoPublisher(cookies_path=cookies_path)
    issues = pub.validate(_bundle(out_root))
    assert issues == [], f"unexpected issues: {issues}"


def test_validate_falls_back_to_toutiao_md_when_canonical_missing(
    tmp_path: Path,
) -> None:
    """bundle.body_path 不存在但 toutiao.md 存在 → 用 toutiao.md。

    与 x_api.py 行为一致：canonical.md 缺失 → 平台子目录 fallback。
    """
    d = tmp_path / "c_fallback"
    d.mkdir(parents=True)
    (d / "toutiao.md").write_text("toutiao body " * 60, encoding="utf-8")
    cookies = tmp_path / "x.json"
    cookies.write_text(json.dumps({"cookies": [{"name": "s", "value": "v"}], "origins": []}))
    pub = ToutiaoPublisher(cookies_path=cookies)
    # body_path 指不存在的路径
    bundle = PostBundle(
        content_id="c_fallback",
        title="Test",
        body_path=d / "canonical.md",  # not created
        media_paths=(),
        tags=(),
        extra={},
    )
    issues = pub.validate(bundle)
    assert issues == [], f"expected toutiao.md fallback, got: {issues}"


# ── publish (dry-run) ──────────────────────────────────────


def test_publish_dry_run_skips_browser(
    out_root: Path, cookies_path: Path, account: AccountConfig,
) -> None:
    """dry_run=True 时不调 health_probe / publish_fn，直接返回模拟结果。"""
    health_probe = MagicMock()
    publish_fn = MagicMock()
    pub = ToutiaoPublisher(
        cookies_path=cookies_path,
        health_probe=health_probe,
        publish_fn=publish_fn,
    )
    result = pub.publish(_bundle(out_root), account, dry_run=True)
    assert result.platform_post_id == "dry-toutiao"
    assert "dry_run" in result.raw_response
    health_probe.assert_not_called()
    publish_fn.assert_not_called()


# ── publish (real path, mocked deps) ──────────────────────


def test_publish_propagates_login_expired(
    out_root: Path, cookies_path: Path, account: AccountConfig,
) -> None:
    """健康检测抛 LoginExpired → publish 直接抛（编排层停止该平台）。"""
    def fake_probe(*a, **kw):
        raise LoginExpired("toutiao/main cookie expired")

    pub = ToutiaoPublisher(
        cookies_path=cookies_path,
        health_probe=fake_probe,
        publish_fn=MagicMock(),
    )
    with pytest.raises(LoginExpired, match="expired"):
        pub.publish(_bundle(out_root), account, dry_run=False)


def test_publish_propagates_health_publish_error(
    out_root: Path, cookies_path: Path, account: AccountConfig,
) -> None:
    """健康检测抛 PublishError（非 LoginExpired）→ publish 抛。"""
    def fake_probe(*a, **kw):
        raise PublishError("network fail")

    pub = ToutiaoPublisher(
        cookies_path=cookies_path,
        health_probe=fake_probe,
        publish_fn=MagicMock(),
    )
    with pytest.raises(PublishError, match="network fail"):
        pub.publish(_bundle(out_root), account, dry_run=False)


def test_publish_calls_publish_fn_when_healthy(
    out_root: Path, cookies_path: Path, account: AccountConfig,
) -> None:
    """健康检测通过 → 调 publish_fn 拿结果。"""
    expected = PublishResult(
        platform_post_id="7123456789",
        url="https://mp.toutiao.com/content/manage?mid=7123456789",
        raw_response="{}",
    )

    def fake_probe(*a, **kw) -> tuple[int, str, str]:
        return (200, "https://mp.toutiao.com/profile_v3/public/", "正常页面")

    def fake_publish(**kw) -> PublishResult:
        return expected

    pub = ToutiaoPublisher(
        cookies_path=cookies_path,
        health_probe=fake_probe,
        publish_fn=fake_publish,
    )
    result = pub.publish(_bundle(out_root), account, dry_run=False)
    assert result is expected


def test_publish_fn_failure_propagates(
    out_root: Path, cookies_path: Path, account: AccountConfig,
) -> None:
    def fake_probe(*a, **kw) -> tuple[int, str, str]:
        return (200, "https://mp.toutiao.com/profile/", "ok")

    def fake_publish(**kw):
        raise PublishError("submit button not found")

    pub = ToutiaoPublisher(
        cookies_path=cookies_path,
        health_probe=fake_probe,
        publish_fn=fake_publish,
    )
    with pytest.raises(PublishError, match="submit button"):
        pub.publish(_bundle(out_root), account, dry_run=False)


# ── extract_post_id ────────────────────────────────────────


def test_extract_post_id_from_mid_query() -> None:
    from pipeline.publishers.toutiao import _extract_post_id
    url = "https://mp.toutiao.com/content/manage?mid=7123456789&foo=bar"
    assert _extract_post_id(url) == "7123456789"


def test_extract_post_id_from_path_segment() -> None:
    from pipeline.publishers.toutiao import _extract_post_id
    url = "https://mp.toutiao.com/content/manage/7123456789012/"
    assert _extract_post_id(url) == "7123456789012"


def test_extract_post_id_returns_none_when_not_found() -> None:
    from pipeline.publishers.toutiao import _extract_post_id
    assert _extract_post_id("https://example.com/no-id-here") is None


# ── selectors 模块 ────────────────────────────────────────


def test_selectors_module_exposes_constants() -> None:
    """防腐层选择器必须集中（HARD_PARTS §2 决策 4）。"""
    from pipeline.publishers import toutiao_selectors as sel
    # 关键选择器字段都在
    assert sel.TITLE_SELECTORS
    assert sel.BODY_SELECTORS
    assert sel.SUBMIT_BUTTON
    assert sel.PUBLISH_URL_FALLBACK
    assert sel.LOGIN_INDICATORS
    # 至少 2 个 fallback
    assert len(sel.TITLE_SELECTORS) >= 2
    assert len(sel.BODY_SELECTORS) >= 2


# ── real_health_probe 在缺 playwright 时正确报错 ──────


def test_real_health_probe_imports_publishwrapper(monkeypatch) -> None:
    """导入路径覆盖（_real_health_probe 内部 lazy import）。"""
    # 不实际跑 chromium（无 playwright 内核 + 无 chromium 二进制）
    # 仅验证 import error 包装正确
    from pipeline.publishers.toutiao import _real_health_probe
    import builtins
    orig_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "playwright.sync_api":
            raise ImportError("simulated missing playwright")
        return orig_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(PublishError, match="playwright not installed"):
        _real_health_probe(Path("/tmp/x.json"), ("https://example.com",))