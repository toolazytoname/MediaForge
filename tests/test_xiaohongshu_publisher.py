"""M4-3 小红书 Publisher 单元测试（TDD）。

测试契约（HARD_PARTS §2 + evaluation-notes §2 集成护栏）：
- platform = 'xiaohongshu'
- subprocess 封装 white0dew/XiaohongshuSkills CLI
- dry_run 不调 subprocess，本地校验完即返回
- 退出码 → 异常映射（EXIT_LOGIN_EXPIRED → LoginExpired）
- 状态行 `PUBLISH_STATUS: <state> <detail>` 解析
- 频控归编排层（不在 adapter 内重复实现）
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
from pipeline.publishers.xiaohongshu import (
    CAPTION_MAX_LEN,
    CAPTION_MIN_LEN,
    EXIT_BAD_BUNDLE,
    EXIT_LOGIN_EXPIRED,
    EXIT_OK,
    EXIT_PLATFORM_ERROR,
    SLIDE_MAX_COUNT,
    SLIDE_MIN_COUNT,
    TAG_MAX_COUNT,
    TAG_MIN_COUNT,
    XiaohongshuPublisher,
    map_exit_code_to_exception,
    parse_publish_status,
)


# ── fixtures ───────────────────────────────────────────────


@pytest.fixture
def skills_dir(tmp_path: Path) -> Path:
    """伪造的 XiaohongshuSkills 安装目录（带 scripts/main.ts）。"""
    s = tmp_path / "xhs-skills"
    (s / "scripts").mkdir(parents=True)
    (s / "scripts" / "main.ts").write_text("// fake", encoding="utf-8")
    return s


@pytest.fixture
def cookies_path(tmp_path: Path) -> Path:
    p = tmp_path / "xhs_main.json"
    p.write_text(json.dumps({"session": "fake"}), encoding="utf-8")
    return p


@pytest.fixture
def account() -> AccountConfig:
    return AccountConfig(
        id="main",
        credentials_path=Path("secrets/cookies/xiaohongshu_main.json"),
    )


@pytest.fixture
def out_root(tmp_path: Path) -> Path:
    """模拟 <content_dir>/xiaohongshu/{slides.json,caption.md,tags.txt}。"""
    d = tmp_path / "c_xhs001"
    d.mkdir(parents=True)
    xhs = d / "xiaohongshu"
    xhs.mkdir(parents=True)
    # 5 张 slide
    slides = [
        {"type": "cover", "title": "封面标题", "body": "封面钩子"},
        {"type": "content", "title": "要点 1", "body": "内容 1"},
        {"type": "content", "title": "要点 2", "body": "内容 2"},
        {"type": "content", "title": "要点 3", "body": "内容 3"},
        {"type": "action", "title": "行动", "body": "关注我"},
    ]
    (xhs / "slides.json").write_text(
        json.dumps(slides, ensure_ascii=False),
        encoding="utf-8",
    )
    (xhs / "caption.md").write_text(
        "这是一段小红书正文，详细介绍这个选题的核心观点，"
        "字数足够通过验证测试。" * 2,
        encoding="utf-8",
    )
    (xhs / "tags.txt").write_text(
        "# comment\n#AI\n#科技\n#开源\n#效率工具\n#测评\n",
        encoding="utf-8",
    )
    return d


def _bundle(out_root: Path, title: str = "Test XHS") -> PostBundle:
    return PostBundle(
        content_id="c_xhs001",
        title=title,
        body_path=out_root / "canonical.md",  # 通常不存在；触发子目录 fallback
        media_paths=(),
        tags=(),
        extra={"platform": "xiaohongshu"},
    )


# ── 构造 ────────────────────────────────────────────────────


def test_init_requires_cookies_path(skills_dir: Path) -> None:
    with pytest.raises(ValueError, match="cookies_path"):
        XiaohongshuPublisher(cookies_path=None, skills_path=skills_dir)  # type: ignore[arg-type]


def test_init_resolves_skills_path_from_env(
    tmp_path: Path, cookies_path: Path, monkeypatch,
) -> None:
    """XHS_SKILLS_PATH env 覆盖默认路径。"""
    custom = tmp_path / "custom-skills"
    (custom / "scripts").mkdir(parents=True)
    (custom / "scripts" / "main.ts").write_text("//", encoding="utf-8")
    monkeypatch.setenv("XHS_SKILLS_PATH", str(custom))
    pub = XiaohongshuPublisher(cookies_path=cookies_path)
    assert pub._skills == custom


# ── validate ────────────────────────────────────────────────


def test_validate_missing_files(
    tmp_path: Path, cookies_path: Path, skills_dir: Path,
) -> None:
    d = tmp_path / "c_empty"
    d.mkdir(parents=True)
    pub = XiaohongshuPublisher(
        cookies_path=cookies_path, skills_path=skills_dir,
    )
    issues = pub.validate(_bundle(d))
    assert any("missing required file" in i for i in issues)
    # 三个文件都应被报
    missing = [i for i in issues if "missing required file" in i]
    assert len(missing) == 3


def test_validate_slides_too_few(
    tmp_path: Path, cookies_path: Path, skills_dir: Path,
) -> None:
    d = tmp_path / "c_few"
    (d / "xiaohongshu").mkdir(parents=True)
    (d / "xiaohongshu" / "slides.json").write_text(
        json.dumps([{"type": "cover", "title": "x", "body": "y"}]),
        encoding="utf-8",
    )
    (d / "xiaohongshu" / "caption.md").write_text("x" * 200, encoding="utf-8")
    (d / "xiaohongshu" / "tags.txt").write_text("#a\n#b\n#c\n", encoding="utf-8")
    pub = XiaohongshuPublisher(
        cookies_path=cookies_path, skills_path=skills_dir,
    )
    issues = pub.validate(_bundle(d))
    assert any(
        f"[{SLIDE_MIN_COUNT}, {SLIDE_MAX_COUNT}]" in i for i in issues
    )


def test_validate_slides_too_many(
    tmp_path: Path, cookies_path: Path, skills_dir: Path,
) -> None:
    d = tmp_path / "c_many"
    (d / "xiaohongshu").mkdir(parents=True)
    slides = [{"type": "x", "title": "x", "body": "y"}] * (SLIDE_MAX_COUNT + 2)
    (d / "xiaohongshu" / "slides.json").write_text(
        json.dumps(slides, ensure_ascii=False), encoding="utf-8",
    )
    (d / "xiaohongshu" / "caption.md").write_text("x" * 200, encoding="utf-8")
    (d / "xiaohongshu" / "tags.txt").write_text("#a\n#b\n#c\n", encoding="utf-8")
    pub = XiaohongshuPublisher(
        cookies_path=cookies_path, skills_path=skills_dir,
    )
    issues = pub.validate(_bundle(d))
    assert any(
        f"[{SLIDE_MIN_COUNT}, {SLIDE_MAX_COUNT}]" in i for i in issues
    )


def test_validate_caption_too_short(
    out_root: Path, cookies_path: Path, skills_dir: Path,
) -> None:
    (out_root / "xiaohongshu" / "caption.md").write_text(
        "太短", encoding="utf-8",
    )
    pub = XiaohongshuPublisher(
        cookies_path=cookies_path, skills_path=skills_dir,
    )
    issues = pub.validate(_bundle(out_root))
    assert any(f"min {CAPTION_MIN_LEN}" in i for i in issues)


def test_validate_caption_too_long(
    out_root: Path, cookies_path: Path, skills_dir: Path,
) -> None:
    (out_root / "xiaohongshu" / "caption.md").write_text(
        "x" * (CAPTION_MAX_LEN + 10), encoding="utf-8",
    )
    pub = XiaohongshuPublisher(
        cookies_path=cookies_path, skills_path=skills_dir,
    )
    issues = pub.validate(_bundle(out_root))
    assert any(f"max {CAPTION_MAX_LEN}" in i for i in issues)


def test_validate_tags_too_few(
    out_root: Path, cookies_path: Path, skills_dir: Path,
) -> None:
    (out_root / "xiaohongshu" / "tags.txt").write_text(
        "#only_one\n", encoding="utf-8",
    )
    pub = XiaohongshuPublisher(
        cookies_path=cookies_path, skills_path=skills_dir,
    )
    issues = pub.validate(_bundle(out_root))
    assert any(
        f"[{TAG_MIN_COUNT}, {TAG_MAX_COUNT}]" in i for i in issues
    )


def test_validate_tags_too_many(
    out_root: Path, cookies_path: Path, skills_dir: Path,
) -> None:
    (out_root / "xiaohongshu" / "tags.txt").write_text(
        "\n".join(f"#tag{i}" for i in range(TAG_MAX_COUNT + 3)),
        encoding="utf-8",
    )
    pub = XiaohongshuPublisher(
        cookies_path=cookies_path, skills_path=skills_dir,
    )
    issues = pub.validate(_bundle(out_root))
    assert any(
        f"[{TAG_MIN_COUNT}, {TAG_MAX_COUNT}]" in i for i in issues
    )


def test_validate_cookies_missing(
    out_root: Path, tmp_path: Path, skills_dir: Path,
) -> None:
    pub = XiaohongshuPublisher(
        cookies_path=tmp_path / "no_such_file.json",
        skills_path=skills_dir,
    )
    issues = pub.validate(_bundle(out_root))
    assert any("cookies/state file missing" in i for i in issues)


def test_validate_all_good(
    out_root: Path, cookies_path: Path, skills_dir: Path,
) -> None:
    pub = XiaohongshuPublisher(
        cookies_path=cookies_path, skills_path=skills_dir,
    )
    issues = pub.validate(_bundle(out_root))
    assert issues == [], f"unexpected: {issues}"


# ── parse_publish_status 纯函数 ────────────────────────────


def test_parse_publish_status_ok() -> None:
    stdout = "some log\nPUBLISH_STATUS: ok saved noteId=abc\nmore log"
    state, detail = parse_publish_status(stdout)
    assert state == "ok"
    assert detail == "saved noteId=abc"


def test_parse_publish_status_failed() -> None:
    stdout = "PUBLISH_STATUS: failed submit button timeout"
    state, detail = parse_publish_status(stdout)
    assert state == "failed"
    assert detail == "submit button timeout"


def test_parse_publish_status_no_marker() -> None:
    state, detail = parse_publish_status("just random output")
    assert state == "unknown"
    assert detail == ""


# ── map_exit_code_to_exception ──────────────────────────────


def test_map_exit_ok_passes() -> None:
    map_exit_code_to_exception(EXIT_OK, "ok", "", platform="x", account_id="a")


def test_map_login_expired_raises_login_expired() -> None:
    with pytest.raises(LoginExpired):
        map_exit_code_to_exception(
            EXIT_LOGIN_EXPIRED, "expired cookie", "",
            platform="x", account_id="a",
        )


def test_map_bad_bundle_raises_publish_error() -> None:
    with pytest.raises(PublishError, match="bad bundle"):
        map_exit_code_to_exception(
            EXIT_BAD_BUNDLE, "missing slides", "",
            platform="x", account_id="a",
        )


def test_map_platform_error_raises_publish_error() -> None:
    with pytest.raises(PublishError, match="publish failed"):
        map_exit_code_to_exception(
            EXIT_PLATFORM_ERROR, "", "submit button not found",
            platform="x", account_id="a",
        )


# ── publish (dry-run) ──────────────────────────────────────


def test_publish_dry_run_skips_subprocess(
    out_root: Path, cookies_path: Path, skills_dir: Path,
    account: AccountConfig,
) -> None:
    runner = MagicMock()
    pub = XiaohongshuPublisher(
        cookies_path=cookies_path, skills_path=skills_dir, runner=runner,
    )
    result = pub.publish(_bundle(out_root), account, dry_run=True)
    assert result.platform_post_id == "dry-xhs"
    assert "dry_run" in result.raw_response
    runner.assert_not_called()


# ── publish (real path, mocked runner) ─────────────────────


def test_publish_calls_cli_with_correct_args(
    out_root: Path, cookies_path: Path, skills_dir: Path,
    account: AccountConfig,
) -> None:
    """subprocess 命令包含必要字段。"""
    runner = MagicMock(return_value=(
        EXIT_OK,
        json.dumps({
            "savedNote": {"noteId": "abc123", "url": "https://www.xiaohongshu.com/explore/abc123"}
        }),
        "",
    ))
    pub = XiaohongshuPublisher(
        cookies_path=cookies_path, skills_path=skills_dir, runner=runner,
    )
    pub.publish(_bundle(out_root), account, dry_run=False)
    runner.assert_called_once()
    cmd = runner.call_args.args[0]
    assert cmd[0] == "npx"
    assert "bun" in cmd
    assert any("main.ts" in c for c in cmd)
    assert "--title" in cmd
    assert "Test XHS" in cmd
    assert "--slides" in cmd
    assert "--caption" in cmd
    assert "--tags" in cmd
    assert "--cookies" in cmd
    assert "--json" in cmd


def test_publish_ok_returns_result(
    out_root: Path, cookies_path: Path, skills_dir: Path,
    account: AccountConfig,
) -> None:
    runner = MagicMock(return_value=(
        EXIT_OK,
        json.dumps({"postId": "xyz", "url": "https://xhs.com/p/xyz"}),
        "",
    ))
    pub = XiaohongshuPublisher(
        cookies_path=cookies_path, skills_path=skills_dir, runner=runner,
    )
    result = pub.publish(_bundle(out_root), account, dry_run=False)
    assert result.platform_post_id == "xyz"
    assert result.url == "https://xhs.com/p/xyz"


def test_publish_login_expired_propagates(
    out_root: Path, cookies_path: Path, skills_dir: Path,
    account: AccountConfig,
) -> None:
    runner = MagicMock(return_value=(
        EXIT_LOGIN_EXPIRED, "expired", "session invalid",
    ))
    pub = XiaohongshuPublisher(
        cookies_path=cookies_path, skills_path=skills_dir, runner=runner,
    )
    with pytest.raises(LoginExpired):
        pub.publish(_bundle(out_root), account, dry_run=False)


def test_publish_partial_state_raises_publish_error(
    out_root: Path, cookies_path: Path, skills_dir: Path,
    account: AccountConfig,
) -> None:
    runner = MagicMock(return_value=(
        EXIT_OK,
        "PUBLISH_STATUS: partial 3 of 5 slides saved",
        "",
    ))
    pub = XiaohongshuPublisher(
        cookies_path=cookies_path, skills_path=skills_dir, runner=runner,
    )
    with pytest.raises(PublishError, match="partial"):
        pub.publish(_bundle(out_root), account, dry_run=False)


def test_publish_unknown_state_treated_as_success_when_exit_ok(
    out_root: Path, cookies_path: Path, skills_dir: Path,
    account: AccountConfig,
) -> None:
    """EXIT_OK + 无 PUBLISH_STATUS 行 → 视为成功（CLI 不总发状态行）。"""
    runner = MagicMock(return_value=(
        EXIT_OK,
        json.dumps({"noteId": "abc", "url": "https://xhs.com/p/abc"}),
        "",
    ))
    pub = XiaohongshuPublisher(
        cookies_path=cookies_path, skills_path=skills_dir, runner=runner,
    )
    result = pub.publish(_bundle(out_root), account, dry_run=False)
    assert result.platform_post_id == "abc"
    assert result.url == "https://xhs.com/p/abc"


def test_publish_failed_state_with_exit_ok_raises_error(
    out_root: Path, cookies_path: Path, skills_dir: Path,
    account: AccountConfig,
) -> None:
    """EXIT_OK + PUBLISH_STATUS: failed → 仍按失败处理（状态行是更高优先级信号）。"""
    runner = MagicMock(return_value=(
        EXIT_OK,
        "PUBLISH_STATUS: failed submit-button-not-found",
        "",
    ))
    pub = XiaohongshuPublisher(
        cookies_path=cookies_path, skills_path=skills_dir, runner=runner,
    )
    with pytest.raises(PublishError, match="failed"):
        pub.publish(_bundle(out_root), account, dry_run=False)


def test_publish_cli_missing_raises_publish_error(
    out_root: Path, cookies_path: Path, tmp_path: Path,
    account: AccountConfig,
) -> None:
    """skills_path 指向不存在的目录 → 友好报错（不让编排层误报未知失败）。"""
    bad = tmp_path / "no-such-skills"
    runner = MagicMock()
    pub = XiaohongshuPublisher(
        cookies_path=cookies_path, skills_path=bad, runner=runner,
    )
    with pytest.raises(PublishError, match="CLI script not found"):
        pub.publish(_bundle(out_root), account, dry_run=False)
    runner.assert_not_called()


def test_publish_runner_timeout_propagates(
    out_root: Path, cookies_path: Path, skills_dir: Path,
    account: AccountConfig,
) -> None:
    """runner 抛 PublishError → 直接抛（不再包一层）。"""
    def fake_runner(cmd, **kw):
        raise PublishError("subprocess timed out")

    pub = XiaohongshuPublisher(
        cookies_path=cookies_path, skills_path=skills_dir,
        runner=fake_runner,
    )
    with pytest.raises(PublishError, match="timed out"):
        pub.publish(_bundle(out_root), account, dry_run=False)