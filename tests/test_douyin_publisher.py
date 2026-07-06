"""M5-2 抖音 Publisher 单元测试。

覆盖契约：
- platform = 'douyin'
- 视频文件必传（media_paths[0]）— validate 不通过时给清晰错误
- AI 生成内容标识（PRD §3.4）：publish 时必勾，缺失 → PublishError
- cookie 失效检测先行
- dry_run 不调浏览器
- AI 占比只接受 low/medium/high（构造时校验）
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
from pipeline.publishers.douyin import (
    AI_RATIO_VALUES,
    DESC_MAX_LEN,
    DESC_MIN_LEN,
    TITLE_MAX_LEN,
    VIDEO_MAX_BYTES,
    DouyinPublisher,
)
from pipeline.publishers import douyin_selectors as sel


# ── fixtures ────────────────────────────────────────


@pytest.fixture
def cookies_path(tmp_path: Path) -> Path:
    p = tmp_path / "douyin_main.json"
    p.write_text(json.dumps({
        "cookies": [{"name": "sessionid", "value": "x", "domain": ".douyin.com", "path": "/"}],
        "origins": [],
    }))
    return p


@pytest.fixture
def account() -> AccountConfig:
    return AccountConfig(
        id="main",
        credentials_path=Path("secrets/cookies/douyin_main.json"),
    )


@pytest.fixture
def out_root(tmp_path: Path) -> Path:
    """content_dir 含 mp4 文件 + 描述文件。"""
    d = tmp_path / "c_dy001"
    d.mkdir(parents=True)
    # 1MB fake mp4（< 128 MB）
    (d / "video.mp4").write_bytes(
        b"\x00\x00\x00\x18ftypisom" + b"\x00" * (1024 * 1024),
    )
    (d / "douyin.md").write_text(
        "这是一段抖音视频描述。介绍 AI 工具的使用方法。",
        encoding="utf-8",
    )
    return d


def _bundle(
    out_root: Path,
    title: str = "Test Douyin Video",
    description: str = "这是一段抖音视频描述，介绍 AI 工具使用。",
    tags: tuple[str, ...] = ("#AI", "#效率"),
) -> PostBundle:
    return PostBundle(
        content_id="c_dy001",
        title=title,
        body_path=out_root / "douyin.md",
        media_paths=(out_root / "video.mp4",),
        tags=tags,
        extra={"description": description, "platform": "douyin"},
    )


# ── 构造 ──────────────────────────────────────────────


def test_init_requires_cookies_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cookies_path"):
        DouyinPublisher(cookies_path=None)  # type: ignore[arg-type]


def test_init_rejects_invalid_ai_ratio(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="ai_ratio"):
        DouyinPublisher(
            cookies_path=tmp_path / "x.json",
            ai_ratio="extreme",   # 不在合法值
        )


def test_init_accepts_all_legal_ai_ratios(tmp_path: Path) -> None:
    for ratio in AI_RATIO_VALUES:
        DouyinPublisher(
            cookies_path=tmp_path / "x.json",
            ai_ratio=ratio,
        )


# ── validate ──────────────────────────────────────────


def test_validate_empty_title(out_root: Path, cookies_path: Path) -> None:
    pub = DouyinPublisher(cookies_path=cookies_path)
    issues = pub.validate(_bundle(out_root, title=""))
    assert any("empty" in i.lower() for i in issues)


def test_validate_title_too_long(out_root: Path, cookies_path: Path) -> None:
    pub = DouyinPublisher(cookies_path=cookies_path)
    issues = pub.validate(_bundle(out_root, title="x" * (TITLE_MAX_LEN + 1)))
    assert any(f"max {TITLE_MAX_LEN}" in i for i in issues)


def test_validate_video_file_missing(
    tmp_path: Path, cookies_path: Path,
) -> None:
    pub = DouyinPublisher(cookies_path=cookies_path)
    # 指向不存在目录
    bundle = PostBundle(
        content_id="c_x", title="ok",
        body_path=tmp_path / "no_such_dir" / "douyin.md",
        media_paths=(),
        tags=(),
        extra={"description": "x" * 20},
    )
    issues = pub.validate(bundle)
    assert any("no video" in i for i in issues)


def test_validate_video_too_large(out_root: Path, cookies_path: Path) -> None:
    # 写一个 130 MB 的假 mp4（< 200 MB，避免 fixture 太大）
    big = out_root / "big.mp4"
    # 用 sparse file 模拟（只 stat 大小，不实际写满）
    big.parent.mkdir(parents=True, exist_ok=True)
    with open(big, "wb") as f:
        f.truncate(VIDEO_MAX_BYTES + 1)
    pub = DouyinPublisher(cookies_path=cookies_path)
    issues = pub.validate(_bundle(out_root).__class__(
        content_id="c_big", title="big",
        body_path=out_root / "douyin.md",
        media_paths=(big,),
        tags=(), extra={"description": "x" * 20},
    ))
    assert any("too large" in i for i in issues)


def test_validate_video_falls_back_to_first_mp4_in_dir(
    tmp_path: Path, cookies_path: Path,
) -> None:
    """media_paths 空 → 自动找 content_dir/*.mp4。"""
    d = tmp_path / "c_auto"
    d.mkdir(parents=True)
    (d / "douyin.md").write_text("desc", encoding="utf-8")
    (d / "video.mp4").write_bytes(b"fake")
    pub = DouyinPublisher(cookies_path=cookies_path)
    bundle = PostBundle(
        content_id="c_auto", title="ok",
        body_path=d / "douyin.md",
        media_paths=(),  # 空 → 触发 fallback
        tags=(), extra={"description": "x" * 20},
    )
    issues = pub.validate(bundle)
    assert issues == [], f"unexpected: {issues}"


def test_validate_cookies_missing(out_root: Path, tmp_path: Path) -> None:
    pub = DouyinPublisher(cookies_path=tmp_path / "nope.json")
    issues = pub.validate(_bundle(out_root))
    assert any("cookies file missing" in i for i in issues)


def test_validate_all_good(out_root: Path, cookies_path: Path) -> None:
    pub = DouyinPublisher(cookies_path=cookies_path)
    issues = pub.validate(_bundle(out_root))
    assert issues == [], f"unexpected: {issues}"


# ── publish dry-run ──────────────────────────────────


def test_publish_dry_run_skips_browser(
    out_root: Path, cookies_path: Path, account: AccountConfig,
) -> None:
    """dry_run=True：不调 health_probe / publish_fn。"""
    health = MagicMock()
    pub_fn = MagicMock()
    pub = DouyinPublisher(
        cookies_path=cookies_path,
        health_probe=health,
        publish_fn=pub_fn,
    )
    result = pub.publish(_bundle(out_root), account, dry_run=True)
    assert result.platform_post_id == "dry-douyin"
    assert "ai_declare_ratio" in result.raw_response
    health.assert_not_called()
    pub_fn.assert_not_called()


def test_dry_run_raw_response_contains_ai_ratio(
    out_root: Path, cookies_path: Path, account: AccountConfig,
) -> None:
    """PRD §3.4：dry_run 也记录 AI 标识占比（供审计）。"""
    pub = DouyinPublisher(cookies_path=cookies_path, ai_ratio="medium")
    result = pub.publish(_bundle(out_root), account, dry_run=True)
    assert '"ai_declare_ratio": "medium"' in result.raw_response


# ── publish 真实路径（mock deps） ──────────────────


def test_publish_propagates_login_expired(
    out_root: Path, cookies_path: Path, account: AccountConfig,
) -> None:
    def fake_probe(*a, **kw):
        raise LoginExpired("douyin/main cookie expired")

    pub = DouyinPublisher(
        cookies_path=cookies_path,
        health_probe=fake_probe,
        publish_fn=MagicMock(),
    )
    with pytest.raises(LoginExpired, match="expired"):
        pub.publish(_bundle(out_root), account, dry_run=False)


def test_publish_propagates_video_missing(
    tmp_path: Path, cookies_path: Path, account: AccountConfig,
) -> None:
    """bundle 没视频 + 目录无 .mp4 → PublishError。"""
    pub = DouyinPublisher(cookies_path=cookies_path)
    bundle = PostBundle(
        content_id="c_x", title="ok",
        body_path=tmp_path / "no_dir" / "douyin.md",
        media_paths=(),
        tags=(), extra={"description": "x" * 20},
    )
    with pytest.raises(PublishError, match="no video"):
        pub.publish(bundle, account, dry_run=False)


def test_publish_calls_real_publish_fn_when_healthy(
    out_root: Path, cookies_path: Path, account: AccountConfig,
) -> None:
    expected = PublishResult(
        platform_post_id="v_abc123",
        url="https://creator.douyin.com/creator-micro/content/manage?video_id=v_abc123",
        raw_response="{}",
    )

    def fake_probe(*a, **kw) -> tuple[int, str, str]:
        return (200, "https://creator.douyin.com/creator-micro/home", "ok")

    def fake_publish(**kw) -> PublishResult:
        # 关键：fake 收到 ai_ratio 参数
        assert kw["ai_ratio"] == "high"
        return expected

    pub = DouyinPublisher(
        cookies_path=cookies_path,
        health_probe=fake_probe,
        publish_fn=fake_publish,
    )
    result = pub.publish(_bundle(out_root), account, dry_run=False)
    assert result is expected


# ── _extract_post_id ──────────────────────────────────


def test_extract_post_id_from_query() -> None:
    from pipeline.publishers.douyin import _extract_post_id
    assert _extract_post_id(
        "https://creator.douyin.com/manage?video_id=7123456789"
    ) == "7123456789"


def test_extract_post_id_from_path() -> None:
    from pipeline.publishers.douyin import _extract_post_id
    assert _extract_post_id(
        "https://www.douyin.com/video/7123456789"
    ) == "7123456789"


def test_extract_post_id_returns_none_when_not_found() -> None:
    from pipeline.publishers.douyin import _extract_post_id
    assert _extract_post_id("https://example.com/no-id-here") is None


# ── 防腐层 ────────────────────────────────────────────


def test_selectors_module_exposes_constants() -> None:
    """HARD_PARTS §2 决策 4：选择器集中 + 多 fallback。"""
    assert sel.TITLE_SELECTORS
    assert sel.DESC_SELECTORS
    assert sel.SUBMIT_BUTTON
    assert sel.PUBLISH_URL_FALLBACK
    # AI 标识（PRD §3.4）
    assert sel.AI_DECLARE_CHECKBOX
    assert sel.AI_DECLARE_RATIO
    # 至少 2 个 fallback
    assert len(sel.TITLE_SELECTORS) >= 2
    assert len(sel.DESC_SELECTORS) >= 2
    assert len(sel.AI_DECLARE_CHECKBOX) >= 2


# ── _declare_ai_content 行为（HARD_PARTS §2 + PRD §3.4） ─


def test_declare_ai_content_raises_when_checkbox_missing() -> None:
    """PRD §3.4：找不到勾选框必须报错，不允许静默忽略。"""
    from pipeline.publishers.douyin import _declare_ai_content, AI_RATIO_VALUES
    page = MagicMock()
    # 模拟 selector 全部找不到
    def fake_locator(css):
        loc = MagicMock()
        loc.count.return_value = 0
        return loc
    page.locator.side_effect = fake_locator

    with pytest.raises(PublishError, match="AI 生成内容勾选框未找到"):
        _declare_ai_content(page, sel, "high")
    assert AI_RATIO_VALUES == ("low", "medium", "high")  # 守卫下拉合法值


def test_declare_ai_content_checks_when_found() -> None:
    """找到勾选框 → 调 .check()。"""
    from pipeline.publishers.douyin import _declare_ai_content
    page = MagicMock()
    # 第一个 selector 命中：page.locator(css).first → 命中 locator
    found_loc = MagicMock()
    found_loc.count.return_value = 1
    found_loc.is_checked.return_value = False

    def make_locator(css):
        loc = MagicMock()
        if css == sel.AI_DECLARE_CHECKBOX[0]:
            loc.first = found_loc
        else:
            empty = MagicMock()
            empty.count.return_value = 0
            loc.first = empty
        return loc
    page.locator.side_effect = make_locator

    _declare_ai_content(page, sel, "high")
    found_loc.check.assert_called_once()


def test_declare_ai_content_skips_check_when_already_checked() -> None:
    from pipeline.publishers.douyin import _declare_ai_content
    page = MagicMock()
    found_loc = MagicMock()
    found_loc.count.return_value = 1
    found_loc.is_checked.return_value = True   # 已勾
    page.locator.return_value.first = found_loc

    _declare_ai_content(page, sel, "high")
    found_loc.check.assert_not_called()