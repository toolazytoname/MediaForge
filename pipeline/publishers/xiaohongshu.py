"""小红书 Publisher（TECH_SPEC §5.2 + HARD_PARTS §2 + evaluation-notes §2）。

**真实 CLI 签名**（2026-05-22 HEAD `988fd2e`，与 M0-0 假设的 bun/TS 不同）：

```
python scripts/publish_pipeline.py \\
  --title "..." | --title-file <path>     # 标题（必填，二选一）
  --content "..." | --content-file <path> # 正文（必填，二选一）
  --image-urls URL [...] | \\
  --images /path/img.jpg [...] | \\
  --video ...                            # 媒体（必填，三选一）
  [--preview]                            # 填充表单不发布
  [--headless]
  [--account <name>]
  [--post-time <datetime>]
```

退出码：0=ok, 1=NOT_LOGGED_IN, 2=error（无 3）。
状态行：`PUBLISH_STATUS: PUBLISHED` 或 `FILL_STATUS: READY_TO_PUBLISH`（--preview 模式）。
无 `--json` 标志 — 输出为纯文本状态行。

**四条集成护栏**（evaluation-notes §2）：
1. mac 优先 — 本任务已 Linux 端到端跑通；真实账号发布留给用户在 mac 跑。
2. vendor 固定 commit — 默认 HEAD `988fd2e`（2026-05-22 fix(_click_tab) 之后），
   CLI 路径走 env `XHS_SKILLS_PATH` 可覆盖。
3. dry_run 分层 — adapter 层 dry_run 不调 CLI，本地校验完即返回；
   XHS 的 `--preview`（填表不发布）只作上线前人工验证档位，**不**走我们
   的 dry_run 路径。
4. 频控归编排层 — XHS 无内建限流，社区有封号案例；safe_publish 把守。

**接口契约**（TECH_SPEC §5.2）：
- platform = 'xiaohongshu'
- validate(bundle) → list[str]：本地校验（不触网络）
- publish(bundle, account, dry_run) → PublishResult：执行发布

**测试友好**：subprocess.run 通过 runner 注入替换；CI 不需 XHS 也可测。
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from pipeline.publishers.base import (
    AccountConfig,
    LoginExpired,
    PostBundle,
    PublishError,
    PublishResult,
    PublisherAdapter,
)


PLATFORM = "xiaohongshu"

# ── 平台硬约束（M2-3 已 hard-limit；这里再校验一次） ──────
CAPTION_MIN_LEN = 50
CAPTION_MAX_LEN = 500
TAG_MIN_COUNT = 3
TAG_MAX_COUNT = 10
SLIDE_MIN_COUNT = 3
SLIDE_MAX_COUNT = 9  # 小红书图文上限 9 图

# ── 默认 vendor 路径与 pin commit ────────────────────────
DEFAULT_SKILLS_PATH = "~/.agents/skills/xiaohongshu-skills"
VENDOR_PIN_COMMIT = "988fd2e"  # 2026-05-22 fix(_click_tab)，M0-0 vendor pin 目标

# ── 退出码约定 ──────────────────────────────────────────
EXIT_OK = 0
EXIT_NOT_LOGGED_IN = 1   # → LoginExpired
EXIT_ERROR = 2           # → PublishError

# ── 状态行解析 ──────────────────────────────────────────
# XHS CLI 输出两种成功状态：
#   - PUBLISH_STATUS: PUBLISHED            (默认发布模式)
#   - FILL_STATUS: READY_TO_PUBLISH        (--preview 模式)
# 失败：
#   - NOT_LOGGED_IN                       (登录失效，无 PUBLISH_STATUS 前缀)
_STATUS_LINE_RE = re.compile(
    r"^(?:PUBLISH_STATUS|FILL_STATUS):\s*(\w+)", re.MULTILINE,
)
_NOT_LOGGED_IN_RE = re.compile(r"^NOT_LOGGED_IN\s*$", re.MULTILINE)


# ── 本地 bundle 解析（ARCHITECTURE §8） ──────────────────


def _resolve_xhs_bundle(bundle: PostBundle) -> tuple[Path, Path, Path]:
    """bundle → (slides.json, caption.md, tags.txt)。

    ARCHITECTURE §8：<content_dir>/xiaohongshu/{slides.json,caption.md,tags.txt}
    """
    base = bundle.body_path.parent / "xiaohongshu"
    return (
        base / "slides.json",
        base / "caption.md",
        base / "tags.txt",
    )


def _parse_tags(text: str) -> list[str]:
    """tags.txt 文本 → list[str]。

    约定：每行一个标签，写作 `#tagname`；注释行 `# ...` 忽略。
    """
    tags: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            after = s[1:]
            if not after or after.startswith(" "):
                continue  # 注释
            tag = after.strip()
        else:
            tag = s
        if tag:
            tags.append(tag)
    return tags


# ── 内容合并（tags 注入最后一行） ────────────────────────


def build_content_with_tags(
    caption_text: str,
    tags: list[str],
) -> str:
    """caption 正文 + 最后一行 `#tag1 #tag2 ...` 拼接。

    XHS skills 用 `_extract_topic_tags_from_last_line` 识别（pyppeteer/feed_explorer
    约定）。我们把 tags.txt 全部 tag 拼到 caption 末尾，最后一行 `#t1 #t2 #t3`。
    """
    body = caption_text.rstrip()
    if tags:
        tag_line = " ".join(
            (t if t.startswith("#") else f"#{t}") for t in tags
        )
        return f"{body}\n\n{tag_line}\n"
    return body + "\n"


# ── PNG 渲染（slides.json → 图卡文件） ───────────────────


def _ensure_rendered_images(
    slides_p: Path,
    out_dir: Path,
    *,
    render_fn: Callable | None = None,
) -> list[Path]:
    """把 slides.json 渲染成 PNG 图卡列表。

    - out_dir 已存在且非空 → 直接返回其中 PNG（幂等：rerun 不重渲染）
    - 否则用 render_fn（默认 pipeline.creators.render.render_cards）渲染
    - 渲染失败 → 抛 PublishError（让编排层记 failed）

    Args:
        slides_p: M2-3 派生产物 slides.json 路径
        out_dir: PNG 输出目录（一般 = <content_dir>/xiaohongshu/images/）
        render_fn: 注入渲染函数（测试 fake / 生产真实 Jinja2+Playwright）

    Returns:
        按 slides 顺序的 PNG 文件路径列表
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(out_dir.glob("*.png"))
    if existing:
        return existing

    try:
        slides = json.loads(slides_p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise PublishError(
            f"slides.json unreadable/invalid JSON: {e}"
        ) from e
    if not isinstance(slides, list) or not slides:
        raise PublishError("slides.json must be non-empty JSON array")

    fn = render_fn
    if fn is None:
        from pipeline.creators.render import render_cards
        fn = render_cards

    paths = fn(
        template="xhs_card",
        slides=slides,
        out_dir=out_dir,
    )
    return list(paths)


# ── 状态行解析（HARD_PARTS §1 决策 4 同源） ─────────────


def parse_publish_status(stdout: str) -> tuple[str, str]:
    """解析 XHS CLI 状态行。

    Returns:
        (state, detail)
        state ∈ {published, ready_to_publish, not_logged_in, unknown}
    """
    # 优先 PUBLISH_STATUS / FILL_STATUS
    m = _STATUS_LINE_RE.search(stdout)
    if m:
        return (m.group(1).lower(), "")
    # 失败：NOT_LOGGED_IN 独立行
    if _NOT_LOGGED_IN_RE.search(stdout):
        return ("not_logged_in", "")
    return ("unknown", "")


# ── 退出码 → 异常 ────────────────────────────────────────


def map_exit_code(
    exit_code: int,
    stdout: str,
    stderr: str,
    *,
    platform: str = PLATFORM,
    account_id: str,
) -> None:
    """subprocess 退出码 → 抛对应异常。

    - 0 → 静默通过（状态行解析由 caller 决定）
    - 1 → LoginExpired（NOT_LOGGED_IN）
    - 2 → PublishError（其它错误）
    - 其它 → PublishError（未知）
    """
    if exit_code == EXIT_OK:
        return
    snippet = (stderr or stdout)[-400:]
    if exit_code == EXIT_NOT_LOGGED_IN:
        raise LoginExpired(
            f"{platform}/{account_id} not logged in: {snippet}"
        )
    # EXIT_ERROR / 其它
    raise PublishError(
        f"{platform}/{account_id} CLI failed (exit={exit_code}): {snippet}"
    )


# ── runner 抽象（subprocess.run 注入） ──────────────────


def _real_subprocess_runner(
    cmd: list[str],
    *,
    timeout_s: float,
    cwd: str | None = None,
) -> tuple[int, str, str]:
    """生产 runner：subprocess.run → (returncode, stdout, stderr)。"""
    try:
        completed = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout_s, cwd=cwd,
        )
    except subprocess.TimeoutExpired as e:
        raise PublishError(
            f"{PLATFORM} CLI timeout after {timeout_s}s"
        ) from e
    except FileNotFoundError as e:
        raise PublishError(
            f"{PLATFORM} CLI not found: {cmd[0] if cmd else '?'}: {e}"
        ) from e
    return (completed.returncode, completed.stdout, completed.stderr)


# ── XiaohongshuPublisher ────────────────────────────────


class XiaohongshuPublisher(PublisherAdapter):
    """小红书 PublisherAdapter（subprocess 封装 XiaohongshuSkills）。"""

    platform = PLATFORM

    def __init__(
        self,
        *,
        # 凭据目录：XHS 自管 Chrome user-data-dir，路径由 cdp_publish.py 解析
        # 我们只需要 skills_path（CLI 在 <skills>/scripts/），不需要单独的 cookies 文件
        skills_path: str | Path | None = None,
        runner: Callable[..., tuple[int, str, str]] | None = None,
        timeout_s: float = 600.0,
        render_fn: Callable | None = None,
        # 调试用：覆盖 vendor pin commit 日志
        vendor_commit: str = VENDOR_PIN_COMMIT,
    ) -> None:
        self._skills = Path(
            skills_path
            or _resolve_skills_path_from_env()
            or DEFAULT_SKILLS_PATH,
        ).expanduser()
        self._runner = runner or _real_subprocess_runner
        self._timeout = timeout_s
        self._render_fn = render_fn
        self._vendor_commit = vendor_commit

    # ── 公开：validate ──

    def validate(self, bundle: PostBundle) -> list[str]:
        """本地校验（不触网络、不调 subprocess）。"""
        issues: list[str] = []
        slides_p, caption_p, tags_p = _resolve_xhs_bundle(bundle)
        # 1. 三件套文件存在
        for p in (slides_p, caption_p, tags_p):
            if not p.exists():
                issues.append(f"missing required file: {p.name} (at {p})")
        if issues:
            return issues

        # 2. slides 数量（决定 PNG 数量）
        try:
            slides = json.loads(slides_p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            issues.append(f"slides.json unreadable/invalid JSON: {e}")
            return issues
        if not isinstance(slides, list):
            issues.append("slides.json must be a JSON array")
            return issues
        if not (SLIDE_MIN_COUNT <= len(slides) <= SLIDE_MAX_COUNT):
            issues.append(
                f"slides count {len(slides)} out of range "
                f"[{SLIDE_MIN_COUNT}, {SLIDE_MAX_COUNT}]"
            )

        # 3. caption 字数
        try:
            caption = caption_p.read_text(encoding="utf-8").strip()
        except OSError as e:
            issues.append(f"caption.md unreadable: {e}")
            return issues
        if len(caption) < CAPTION_MIN_LEN:
            issues.append(
                f"caption too short: {len(caption)} chars "
                f"(min {CAPTION_MIN_LEN})"
            )
        if len(caption) > CAPTION_MAX_LEN:
            issues.append(
                f"caption too long: {len(caption)} chars "
                f"(max {CAPTION_MAX_LEN})"
            )

        # 4. tags 数量
        try:
            tags = _parse_tags(tags_p.read_text(encoding="utf-8"))
        except OSError as e:
            issues.append(f"tags.txt unreadable: {e}")
            return issues
        if not (TAG_MIN_COUNT <= len(tags) <= TAG_MAX_COUNT):
            issues.append(
                f"tags count {len(tags)} out of range "
                f"[{TAG_MIN_COUNT}, {TAG_MAX_COUNT}]"
            )

        return issues

    # ── 公开：publish ──

    def publish(
        self,
        bundle: PostBundle,
        account: AccountConfig,
        dry_run: bool = False,
    ) -> PublishResult:
        slides_p, caption_p, tags_p = _resolve_xhs_bundle(bundle)

        # dry-run：不调 CLI / 不渲染 PNG，本地校验完即返回
        if dry_run:
            return PublishResult(
                platform_post_id="dry-xhs",
                url=None,
                raw_response=json.dumps({
                    "dry_run": True,
                    "platform": PLATFORM,
                    "account": account.id,
                    "slides_file": str(slides_p),
                    "caption_chars": len(caption_p.read_text(encoding="utf-8")),
                    "tags_count": len(
                        _parse_tags(tags_p.read_text(encoding="utf-8"))
                    ),
                }, ensure_ascii=False),
            )

        # 0. CLI 路径校验
        cli_script = self._skills / "scripts" / "publish_pipeline.py"
        if not cli_script.exists():
            raise PublishError(
                f"{PLATFORM} CLI script not found: {cli_script}. "
                f"Clone white0dew/XiaohongshuSkills (HEAD: "
                f"{self._vendor_commit}) and set XHS_SKILLS_PATH, or "
                f"set platforms.xiaohongshu.skills_path in config."
            )

        # 1. slides.json → PNG 图卡
        images_dir = slides_p.parent / "images"
        try:
            png_paths = _ensure_rendered_images(
                slides_p, images_dir, render_fn=self._render_fn,
            )
        except Exception as e:
            raise PublishError(
                f"{PLATFORM}/{account.id} render slides failed: {e}"
            ) from e
        if not png_paths:
            raise PublishError(
                f"{PLATFORM}/{account.id} no PNG images rendered"
            )

        # 2. caption + tags → 合并 content 临时文件
        caption_text = caption_p.read_text(encoding="utf-8")
        tags = _parse_tags(tags_p.read_text(encoding="utf-8"))
        merged_content = build_content_with_tags(caption_text, tags)

        # 写 tmp 文件（HARD_PARTS §5 幂等 → .tmp + rename）
        content_tmp = slides_p.parent / "_merged_content.tmp"
        content_final = slides_p.parent / "_merged_content.txt"
        content_tmp.write_text(merged_content, encoding="utf-8")
        content_tmp.rename(content_final)

        # 3. 拼装 CLI 命令
        cmd = [
            "python", str(cli_script),
            "--title", bundle.title,
            "--content-file", str(content_final),
            "--images", *[str(p) for p in png_paths],
            "--headless",
            "--account", account.id,
        ]

        # 4. 跑 subprocess
        exit_code, stdout, stderr = self._runner(
            cmd, timeout_s=self._timeout,
        )

        # 5. 退出码 → 异常
        map_exit_code(
            exit_code, stdout, stderr,
            account_id=account.id,
        )

        # 6. 状态行解析
        state, detail = parse_publish_status(stdout)
        if state == "not_logged_in":
            raise LoginExpired(
                f"{PLATFORM}/{account.id} CLI said NOT_LOGGED_IN "
                f"(exit={exit_code})"
            )
        if state in ("published", "ready_to_publish"):
            post_id, url = _extract_post_result(stdout)
            return PublishResult(
                platform_post_id=post_id,
                url=url,
                raw_response=stdout[-4000:],  # 截断防爆
            )
        # state == "unknown" + EXIT_OK → CLI 退出 0 但没发状态行
        # 视作软成功，URL/post_id 拿不到也返回（编排层靠 exit_code 判定）
        return PublishResult(
            platform_post_id=None,
            url=None,
            raw_response=stdout[-4000:],
        )


# ── helpers ─────────────────────────────────────────────


def _extract_post_result(stdout: str) -> tuple[str | None, str | None]:
    """从 stdout 启发式提取 (post_id, url)。

    XHS CLI 当前 HEAD 无 `--json` 出口，stdout 是状态行 + 调试输出。
    启发式：找 `noteId=...` / `posted URL: ...` / `explore/<id>` 模式。
    """
    # 1. noteId=xxx 或 note_id=xxx（id 允许字母数字下划线横线）
    m = re.search(
        r"note[_]?[Ii]d\s*[=:]\s*([a-zA-Z0-9_-]+)", stdout,
    )
    if m:
        post_id = m.group(1)
        url_m = re.search(
            r"(https?://[^\s]*xiaohongshu\.com/[^\s]*" + post_id + r"[^\s]*)",
            stdout,
        )
        return (post_id, url_m.group(1) if url_m else None)
    # 2. URL 末段纯 id
    m = re.search(
        r"https?://(?:www\.)?xiaohongshu\.com/explore/([a-zA-Z0-9_-]+)",
        stdout,
    )
    if m:
        return (m.group(1), m.group(0))
    # 3. posted URL 提示
    m = re.search(
        r"(https?://[^\s]*xiaohongshu\.com/[^\s]+)",
        stdout,
    )
    if m:
        return (None, m.group(1))
    return (None, None)


def _resolve_skills_path_from_env() -> str | None:
    """XHS_SKILLS_PATH 环境变量解析（供 vendor 路径覆盖）。"""
    import os
    return os.environ.get("XHS_SKILLS_PATH")


__all__ = [
    "XiaohongshuPublisher",
    "parse_publish_status",
    "map_exit_code",
    "build_content_with_tags",
    "EXIT_OK",
    "EXIT_NOT_LOGGED_IN",
    "EXIT_ERROR",
    "DEFAULT_SKILLS_PATH",
    "VENDOR_PIN_COMMIT",
]