"""M4-3 小红书 Publisher 单元测试（TDD — 适配真实 HEAD CLI）。

真实 CLI（2026-05-22 HEAD 988fd2e）：
- python scripts/publish_pipeline.py
- args: --title, --content-file, --images, --headless, --account
- exit codes: 0=ok, 1=NOT_LOGGED_IN, 2=error
- 状态行：PUBLISH_STATUS: PUBLISHED / FILL_STATUS: READY_TO_PUBLISH / NOT_LOGGED_IN
- 无 --json / --cookies / --slides / --tags 标志
- tags 嵌入 content 最后一行 `#t1 #t2`

测试覆盖：
- 本地 validate（不触网络、不调 CLI）
- CLI 命令构造（含真实 flag 名称）
- 状态行解析（4 种 state）
- 退出码映射
- tags 注入最后一行
- 渲染步骤（mock render_fn）
- dry_run 分层
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
    EXIT_ERROR,
    EXIT_NOT_LOGGED_IN,
    EXIT_OK,
    SLIDE_MAX_COUNT,
    SLIDE_MIN_COUNT,
    TAG_MAX_COUNT,
    TAG_MIN_COUNT,
    VENDOR_PIN_COMMIT,
    XiaohongshuPublisher,
    build_content_with_tags,
    map_exit_code,
    parse_publish_status,
)


# ── fixtures ────────────────────────────────────────────


@pytest.fixture
def skills_dir(tmp_path: Path) -> Path:
    """伪造的 XiaohongshuSkills 安装目录（带 scripts/publish_pipeline.py）。"""
    s = tmp_path / "xhs-skills"
    (s / "scripts").mkdir(parents=True)
    (s / "scripts" / "publish_pipeline.py").write_text(
        "# fake CLI", encoding="utf-8",
    )
    return s


@pytest.fixture
def account() -> AccountConfig:
    """XHS 自管 login state；AccountConfig.credentials_path 不再被使用。"""
    return AccountConfig(
        id="main",
        credentials_path=Path("unused"),  # 保留字段兼容性
    )


@pytest.fixture
def out_root(tmp_path: Path) -> Path:
    """<content_dir>/xiaohongshu/{slides.json, caption.md, tags.txt}。"""
    d = tmp_path / "c_xhs001"
    d.mkdir(parents=True)
    xhs = d / "xiaohongshu"
    xhs.mkdir(parents=True)
    slides = [
        {"type": "cover", "title": "封面", "body": "钩子"},
        {"type": "content", "title": "点 1", "body": "内 1"},
        {"type": "content", "title": "点 2", "body": "内 2"},
        {"type": "content", "title": "点 3", "body": "内 3"},
        {"type": "action", "title": "行动", "body": "关注"},
    ]
    (xhs / "slides.json").write_text(
        json.dumps(slides, ensure_ascii=False), encoding="utf-8",
    )
    (xhs / "caption.md").write_text(
        "这是一段小红书正文，详细介绍这个选题的核心观点，"
        "字数足够通过验证测试。" * 2,
        encoding="utf-8",
    )
    (xhs / "tags.txt").write_text(
        "# 注释行\n#AI\n#科技\n#开源\n#效率\n#测评\n",
        encoding="utf-8",
    )
    return d


def _bundle(out_root: Path, title: str = "Test XHS") -> PostBundle:
    return PostBundle(
        content_id="c_xhs001",
        title=title,
        body_path=out_root / "canonical.md",  # 不存在；触发 fallback
        media_paths=(),
        tags=(),
        extra={"platform": "xiaohongshu"},
    )


def _fake_render(
    template: str, slides: list, out_dir: Path, **kw,
) -> list[Path]:
    """测试用 render_fn：写假 PNG（不调 chromium）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, _ in enumerate(slides):
        p = out_dir / f"slide_{i:03d}.png"
        p.write_bytes(b"\x89PNG_FAKE")  # 不是真 PNG，但路径存在即可
        paths.append(p)
    return paths


# ── 构造 ─────────────────────────────────────────────────


def test_init_resolves_skills_path(
    tmp_path: Path, monkeypatch,
) -> None:
    """XHS_SKILLS_PATH env 覆盖默认。"""
    custom = tmp_path / "custom-skills"
    (custom / "scripts").mkdir(parents=True)
    (custom / "scripts" / "publish_pipeline.py").write_text(
        "#", encoding="utf-8",
    )
    monkeypatch.setenv("XHS_SKILLS_PATH", str(custom))
    pub = XiaohongshuPublisher()
    assert pub._skills == custom


def test_init_uses_default_when_no_env(
    tmp_path: Path, monkeypatch,
) -> None:
    """未设 env → 默认路径（~ 展开后包含 .agents/skills/xiaohongshu-skills）。"""
    monkeypatch.delenv("XHS_SKILLS_PATH", raising=False)
    pub = XiaohongshuPublisher()
    # 展开后是绝对路径；只断言末段
    assert str(pub._skills).endswith(".agents/skills/xiaohongshu-skills")


def test_vendor_pin_commit_recorded() -> None:
    """vendor pin commit 是常量，集成时核对 HEAD 不漂移。"""
    assert VENDOR_PIN_COMMIT == "988fd2e"


# ── validate ────────────────────────────────────────────


def test_validate_missing_files(
    tmp_path: Path, skills_dir: Path,
) -> None:
    d = tmp_path / "c_empty"
    d.mkdir(parents=True)
    pub = XiaohongshuPublisher(skills_path=skills_dir)
    issues = pub.validate(_bundle(d))
    assert any("missing required file" in i for i in issues)
    assert len([i for i in issues if "missing" in i]) == 3


def test_validate_slides_too_few(
    tmp_path: Path, skills_dir: Path,
) -> None:
    d = tmp_path / "c_few"
    (d / "xiaohongshu").mkdir(parents=True)
    (d / "xiaohongshu" / "slides.json").write_text(
        json.dumps([{"type": "cover", "title": "x", "body": "y"}]),
        encoding="utf-8",
    )
    (d / "xiaohongshu" / "caption.md").write_text("x" * 200, encoding="utf-8")
    (d / "xiaohongshu" / "tags.txt").write_text("#a\n#b\n#c\n", encoding="utf-8")
    pub = XiaohongshuPublisher(skills_path=skills_dir)
    issues = pub.validate(_bundle(d))
    assert any(
        f"[{SLIDE_MIN_COUNT}, {SLIDE_MAX_COUNT}]" in i for i in issues
    )


def test_validate_caption_too_short(
    out_root: Path, skills_dir: Path,
) -> None:
    (out_root / "xiaohongshu" / "caption.md").write_text("太短", encoding="utf-8")
    pub = XiaohongshuPublisher(skills_path=skills_dir)
    issues = pub.validate(_bundle(out_root))
    assert any(f"min {CAPTION_MIN_LEN}" in i for i in issues)


def test_validate_caption_too_long(
    out_root: Path, skills_dir: Path,
) -> None:
    (out_root / "xiaohongshu" / "caption.md").write_text(
        "x" * (CAPTION_MAX_LEN + 10), encoding="utf-8",
    )
    pub = XiaohongshuPublisher(skills_path=skills_dir)
    issues = pub.validate(_bundle(out_root))
    assert any(f"max {CAPTION_MAX_LEN}" in i for i in issues)


def test_validate_tags_too_few(
    out_root: Path, skills_dir: Path,
) -> None:
    (out_root / "xiaohongshu" / "tags.txt").write_text(
        "#only_one\n", encoding="utf-8",
    )
    pub = XiaohongshuPublisher(skills_path=skills_dir)
    issues = pub.validate(_bundle(out_root))
    assert any(
        f"[{TAG_MIN_COUNT}, {TAG_MAX_COUNT}]" in i for i in issues
    )


def test_validate_all_good(
    out_root: Path, skills_dir: Path,
) -> None:
    pub = XiaohongshuPublisher(skills_path=skills_dir)
    issues = pub.validate(_bundle(out_root))
    assert issues == [], f"unexpected: {issues}"


# ── build_content_with_tags（纯函数） ─────────────────


def test_build_content_with_tags_no_tags() -> None:
    out = build_content_with_tags("正文内容", [])
    assert "正文内容" in out
    assert "#" not in out


def test_build_content_with_tags_appends_last_line() -> None:
    out = build_content_with_tags("正文", ["AI", "科技", "开源"])
    lines = out.strip().split("\n")
    assert lines[-1] == "#AI #科技 #开源"


def test_build_content_with_tags_preserves_hashtag_prefix() -> None:
    """tag 已带 `#` 前缀时不重复加。"""
    out = build_content_with_tags("正文", ["#AI", "科技"])
    assert out.strip().endswith("#AI #科技")


# ── parse_publish_status ────────────────────────────────


def test_parse_publish_status_published() -> None:
    state, _ = parse_publish_status(
        "log...\nPUBLISH_STATUS: PUBLISHED\nmore log"
    )
    assert state == "published"


def test_parse_publish_status_ready_to_publish() -> None:
    """--preview 模式 → FILL_STATUS: READY_TO_PUBLISH。"""
    state, _ = parse_publish_status(
        "log...\nFILL_STATUS: READY_TO_PUBLISH\n"
    )
    assert state == "ready_to_publish"


def test_parse_publish_status_not_logged_in() -> None:
    state, _ = parse_publish_status("NOT_LOGGED_IN\n")
    assert state == "not_logged_in"


def test_parse_publish_status_unknown() -> None:
    state, _ = parse_publish_status("random output no markers")
    assert state == "unknown"


# ── map_exit_code ──────────────────────────────────────


def test_map_exit_ok_passes() -> None:
    map_exit_code(EXIT_OK, "ok", "", account_id="a")


def test_map_exit_not_logged_in_raises_login_expired() -> None:
    with pytest.raises(LoginExpired, match="not logged in"):
        map_exit_code(
            EXIT_NOT_LOGGED_IN, "expired", "",
            account_id="a",
        )


def test_map_exit_error_raises_publish_error() -> None:
    with pytest.raises(PublishError, match="CLI failed"):
        map_exit_code(EXIT_ERROR, "", "submit button not found", account_id="a")


# ── publish (dry-run) ──────────────────────────────────


def test_publish_dry_run_skips_everything(
    out_root: Path, skills_dir: Path, account: AccountConfig,
) -> None:
    runner = MagicMock()
    pub = XiaohongshuPublisher(
        skills_path=skills_dir, runner=runner, render_fn=_fake_render,
    )
    result = pub.publish(_bundle(out_root), account, dry_run=True)
    assert result.platform_post_id == "dry-xhs"
    runner.assert_not_called()


# ── publish (real path, mocked runner + render) ───────


def test_publish_calls_real_python_cli(
    out_root: Path, skills_dir: Path, account: AccountConfig,
) -> None:
    """CLI 命令用 python + publish_pipeline.py + 真实 flag 名称。"""
    runner = MagicMock(return_value=(
        EXIT_OK, "PUBLISH_STATUS: PUBLISHED noteId=abc123", "",
    ))
    pub = XiaohongshuPublisher(
        skills_path=skills_dir, runner=runner, render_fn=_fake_render,
    )
    pub.publish(_bundle(out_root), account, dry_run=False)
    cmd = runner.call_args.args[0]
    assert cmd[0] == "python"
    assert "publish_pipeline.py" in cmd[1]
    assert "--title" in cmd
    assert "Test XHS" in cmd
    assert "--content-file" in cmd
    assert "--images" in cmd
    assert "--headless" in cmd
    assert "--account" in cmd
    assert "main" in cmd
    # 失败/不存在标志
    for forbidden in ("--json", "--cookies", "--slides", "--tags", "--caption"):
        assert forbidden not in cmd, f"forbidden flag {forbidden} found"


def test_publish_merges_tags_into_content_file(
    out_root: Path, skills_dir: Path, account: AccountConfig,
) -> None:
    """tags.txt 内容被注入最后一行 `#t1 #t2 ...`。"""
    runner = MagicMock(return_value=(
        EXIT_OK, "PUBLISH_STATUS: PUBLISHED", "",
    ))
    pub = XiaohongshuPublisher(
        skills_path=skills_dir, runner=runner, render_fn=_fake_render,
    )
    pub.publish(_bundle(out_root), account, dry_run=False)
    cmd = runner.call_args.args[0]
    # 找到 --content-file 后面的路径
    idx = cmd.index("--content-file")
    content_path = Path(cmd[idx + 1])
    assert content_path.exists()
    content = content_path.read_text(encoding="utf-8")
    # 5 个 tag 应被注入
    assert content.strip().endswith("#AI #科技 #开源 #效率 #测评")


def test_publish_renders_slides_to_png(
    out_root: Path, skills_dir: Path, account: AccountConfig,
) -> None:
    """slides.json → PNG 图卡列表传 --images。"""
    runner = MagicMock(return_value=(
        EXIT_OK, "PUBLISH_STATUS: PUBLISHED", "",
    ))
    pub = XiaohongshuPublisher(
        skills_path=skills_dir, runner=runner, render_fn=_fake_render,
    )
    pub.publish(_bundle(out_root), account, dry_run=False)
    cmd = runner.call_args.args[0]
    idx = cmd.index("--images")
    # --images 后到下一个 flag 前都是 PNG 路径
    png_paths = []
    for c in cmd[idx + 1:]:
        if c.startswith("--"):
            break
        png_paths.append(c)
    assert len(png_paths) == 5, f"expected 5 slides, got {len(png_paths)}"
    # 文件确实存在
    for p in png_paths:
        assert Path(p).exists()


def test_publish_ok_extracts_post_id_and_url(
    out_root: Path, skills_dir: Path, account: AccountConfig,
) -> None:
    """PUBLISH_STATUS + URL → PublishResult。"""
    stdout = (
        "[pipeline] Published!\n"
        "PUBLISH_STATUS: PUBLISHED\n"
        "URL: https://www.xiaohongshu.com/explore/abc123def\n"
    )
    runner = MagicMock(return_value=(EXIT_OK, stdout, ""))
    pub = XiaohongshuPublisher(
        skills_path=skills_dir, runner=runner, render_fn=_fake_render,
    )
    result = pub.publish(_bundle(out_root), account, dry_run=False)
    assert result.platform_post_id == "abc123def"
    assert result.url == "https://www.xiaohongshu.com/explore/abc123def"


def test_publish_preview_mode_yields_ready_to_publish(
    out_root: Path, skills_dir: Path, account: AccountConfig,
) -> None:
    """FILL_STATUS → 视为成功。"""
    runner = MagicMock(return_value=(
        EXIT_OK, "FILL_STATUS: READY_TO_PUBLISH", "",
    ))
    pub = XiaohongshuPublisher(
        skills_path=skills_dir, runner=runner, render_fn=_fake_render,
    )
    result = pub.publish(_bundle(out_root), account, dry_run=False)
    assert result.raw_response  # 非空


def test_publish_not_logged_in_exit_1_propagates(
    out_root: Path, skills_dir: Path, account: AccountConfig,
) -> None:
    runner = MagicMock(return_value=(EXIT_NOT_LOGGED_IN, "NOT_LOGGED_IN", ""))
    pub = XiaohongshuPublisher(
        skills_path=skills_dir, runner=runner, render_fn=_fake_render,
    )
    with pytest.raises(LoginExpired):
        pub.publish(_bundle(out_root), account, dry_run=False)


def test_publish_cli_exit_2_propagates_publish_error(
    out_root: Path, skills_dir: Path, account: AccountConfig,
) -> None:
    runner = MagicMock(return_value=(EXIT_ERROR, "", "no selector"))
    pub = XiaohongshuPublisher(
        skills_path=skills_dir, runner=runner, render_fn=_fake_render,
    )
    with pytest.raises(PublishError, match="CLI failed"):
        pub.publish(_bundle(out_root), account, dry_run=False)


def test_publish_cli_missing_raises_clear_error(
    out_root: Path, tmp_path: Path, account: AccountConfig,
) -> None:
    """skills_path 不存在 → 友好报错。"""
    bad = tmp_path / "no-such-skills"
    runner = MagicMock()
    pub = XiaohongshuPublisher(
        skills_path=bad, runner=runner, render_fn=_fake_render,
    )
    with pytest.raises(PublishError, match="CLI script not found"):
        pub.publish(_bundle(out_root), account, dry_run=False)
    runner.assert_not_called()


def test_publish_render_failure_wrapped(
    out_root: Path, skills_dir: Path, account: AccountConfig,
) -> None:
    """渲染失败 → PublishError（不让 raw 异常冒出）。"""
    def bad_render(*a, **kw):
        raise RuntimeError("chromium crashed")

    runner = MagicMock()
    pub = XiaohongshuPublisher(
        skills_path=skills_dir, runner=runner, render_fn=bad_render,
    )
    with pytest.raises(PublishError, match="render slides failed"):
        pub.publish(_bundle(out_root), account, dry_run=False)


def test_publish_runner_timeout_propagates(
    out_root: Path, skills_dir: Path, account: AccountConfig,
) -> None:
    def fake_runner(cmd, **kw):
        raise PublishError("subprocess timed out")

    pub = XiaohongshuPublisher(
        skills_path=skills_dir, runner=fake_runner, render_fn=_fake_render,
    )
    with pytest.raises(PublishError, match="timed out"):
        pub.publish(_bundle(out_root), account, dry_run=False)


# ── 幂等（rerun 不重渲染）────────────────────────────


def test_publish_does_not_rerender_when_pngs_exist(
    out_root: Path, skills_dir: Path, account: AccountConfig,
) -> None:
    """PNG 已存在 → 跳过渲染（幂等）。"""
    images_dir = out_root / "xiaohongshu" / "images"
    images_dir.mkdir(parents=True)
    existing = images_dir / "slide_001.png"
    existing.write_bytes(b"already here")

    render_called = MagicMock(return_value=[existing])
    runner = MagicMock(return_value=(EXIT_OK, "PUBLISH_STATUS: PUBLISHED", ""))
    pub = XiaohongshuPublisher(
        skills_path=skills_dir, runner=runner, render_fn=render_called,
    )
    pub.publish(_bundle(out_root), account, dry_run=False)
    render_called.assert_not_called()  # 已存在 → 不重渲染


# ── end-to-end subprocess smoke ────────────────────────


def test_publish_against_real_fake_cli(
    tmp_path: Path, skills_dir: Path, account: AccountConfig,
) -> None:
    """End-to-end：写一个真 fake publish_pipeline.py，subprocess 真跑。

    不依赖 XHS 项目依赖 / Chrome / 网络。验证 CLI 调用结构 + 退出码解析 +
    状态行解析端到端联通。
    """
    out_root = tmp_path / "c_e2e"
    out_root.mkdir(parents=True)
    xhs = out_root / "xiaohongshu"
    xhs.mkdir(parents=True)
    slides = [{"type": "x", "title": "t", "body": "b"}] * 5
    (xhs / "slides.json").write_text(json.dumps(slides), encoding="utf-8")
    (xhs / "caption.md").write_text("x" * 200, encoding="utf-8")
    (xhs / "tags.txt").write_text("#a\n#b\n#c\n", encoding="utf-8")

    # 用真 fake CLI 替换 publish_pipeline.py
    fake_cli = skills_dir / "scripts" / "publish_pipeline.py"
    fake_cli.write_text(
        "#!/usr/bin/env python3\n"
        "import argparse, sys\n"
        "p = argparse.ArgumentParser()\n"
        "p.add_argument('--title', required=True)\n"
        "p.add_argument('--content-file', required=True)\n"
        "p.add_argument('--images', nargs='+', required=True)\n"
        "p.add_argument('--headless', action='store_true')\n"
        "p.add_argument('--account', required=True)\n"
        "args = p.parse_args()\n"
        # 验证参数合法性（端到端契约）
        "assert args.title, 'title empty'\n"
        "assert len(args.images) >= 3, f'need >=3 images, got {len(args.images)}'\n"
        "content = open(args.content_file, encoding='utf-8').read()\n"
        "assert '#a' in content, 'tag missing in content'\n"
        # 输出状态行（成功）
        "print('PUBLISH_STATUS: PUBLISHED', flush=True)\n"
        "print('noteId=ne_e2e_id', flush=True)\n"
        "print('URL: https://www.xiaohongshu.com/explore/ne_e2e_id', flush=True)\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )

    pub = XiaohongshuPublisher(skills_path=skills_dir, render_fn=_fake_render)
    result = pub.publish(_bundle(out_root), account, dry_run=False)
    assert result.platform_post_id == "ne_e2e_id"
    assert result.url == "https://www.xiaohongshu.com/explore/ne_e2e_id"


def test_publish_real_subprocess_login_expired(
    tmp_path: Path, skills_dir: Path, account: AccountConfig,
) -> None:
    """真 subprocess：CLI 模拟 NOT_LOGGED_IN + exit 1 → LoginExpired。"""
    out_root = tmp_path / "c_li"
    out_root.mkdir(parents=True)
    xhs = out_root / "xiaohongshu"
    xhs.mkdir(parents=True)
    slides = [{"type": "x", "title": "t", "body": "b"}] * 5
    (xhs / "slides.json").write_text(json.dumps(slides), encoding="utf-8")
    (xhs / "caption.md").write_text("x" * 200, encoding="utf-8")
    (xhs / "tags.txt").write_text("#a\n#b\n#c\n", encoding="utf-8")

    fake_cli = skills_dir / "scripts" / "publish_pipeline.py"
    fake_cli.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "print('NOT_LOGGED_IN', flush=True)\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )

    pub = XiaohongshuPublisher(skills_path=skills_dir, render_fn=_fake_render)
    with pytest.raises(LoginExpired, match="not logged in"):
        pub.publish(_bundle(out_root), account, dry_run=False)