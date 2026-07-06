"""小红书 Publisher（TECH_SPEC §5.2 + HARD_PARTS §2 + evaluation-notes §2 集成护栏）。

subprocess 封装 `white0dew/XiaohongshuSkills` CLI 进 PublisherAdapter。

四条护栏（evaluation-notes §2）：
1. mac 冒烟先行 — 测试环境友好；真实冒烟用户拿 mac 跑。
2. vendor 固定 commit — CLI 路径走 config / env，不追 HEAD；
   2026-05-21 `fix(_click_tab)` 之后的 main。
3. dry_run 语义分层 — 契约的 `dry_run=True` 在 adapter 层校验 bundle 即返回、
   完全不碰浏览器；其 `--preview` 模式（填充表单不点发布）**不**走
   我们的 dry_run 路径（仅作 M4 上线前人工验证档位）。
4. 频控归编排层 — XiaohongshuSkills 无内建限流，社区有封号案例；
   单账号日 ≤ 3 帖 + 间隔 ≥ 4h 由 safe_publish / config.publish 把守。

接口契约（TECH_SPEC §5.2）：
- platform = 'xiaohongshu'
- validate(bundle) → list[str]：本地校验
- publish(bundle, account, dry_run) → PublishResult：执行发布

测试友好：
- subprocess.run 通过注入（runner）替换；CI 无 b  也不跑真实 subprocess
- 解析 `PUBLISH_STATUS:` / exit code 走纯函数，单元测试覆盖
"""
from __future__ import annotations

import json
import re
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
SLIDE_MAX_COUNT = 7   # 小红书图文上限 9（最多 9 图），但 5-7 最常见

# ── XiaohongshuSkills CLI 默认路径 ────────────────────────
DEFAULT_SKILLS_PATH = (
    "~/.agents/skills/xiaohongshu-skills"   # Linux/Mac 默认安装位置
)

# ── 退出码约定（与 XiaohongshuSkills README 一致） ──────────
EXIT_OK = 0
EXIT_LOGIN_EXPIRED = 1
EXIT_BAD_BUNDLE = 2
EXIT_PLATFORM_ERROR = 3


# ── 本地校验 ────────────────────────────────────────────────


def _resolve_xhs_bundle(bundle: PostBundle) -> tuple[Path, Path, Path]:
    """bundle → (slides.json, caption.md, tags.txt) 三件套。

    ARCHITECTURE §8：<content_dir>/xiaohongshu/{slides.json,caption.md,tags.txt}

    返回三个文件路径；任一缺失抛 FileNotFoundError（由 validate 转 issue）。
    """
    base = bundle.body_path.parent / "xiaohongshu"
    slides_p = base / "slides.json"
    caption_p = base / "caption.md"
    tags_p = base / "tags.txt"
    return slides_p, caption_p, tags_p


def _parse_tags(text: str) -> list[str]:
    """tags.txt 文本 → list[str]。

    文件格式约定：
      - 每行一个标签，写作 `#tagname`（无空格）
      - 注释行：开头为 `# `（# 后跟空格）或纯空行
      - 平台会按字面写入标签（自动补 # 前缀）
    """
    tags: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        # 注释：# 后面跟空白/字符（不是直接的 tag）
        # 用正则：`# ` 或 `#xx`（非 tag） vs `#tagname`（连续无空格的字母数字下划线中文）
        if s.startswith("#"):
            after = s[1:]
            if not after or after.startswith(" "):
                # 注释或 `#` 单独
                continue
            # `#tag` 或 `#tagname` 形式 → 取 after
            tag = after.strip()
        else:
            # 没 `#` 前缀也算 tag（如 `tag`）
            tag = s
        if tag:
            tags.append(tag)
    return tags


# ── 状态行解析（HARD_PARTS §1 决策 4 同源） ────────────────


_PUBLISH_STATUS_RE = re.compile(
    r"^PUBLISH_STATUS:\s*(\w+)(?:\s+(.*))?$", re.MULTILINE,
)


def parse_publish_status(stdout: str) -> tuple[str, str]:
    """解析 XiaohongshuSkills 的 `PUBLISH_STATUS: <state> <detail>` 状态行。

    Returns:
        (state, detail) 元组；找不到状态行 → ("unknown", "")。
        state ∈ {ok, partial, failed}（XiaohongshuSkills 自定义）。
    """
    m = _PUBLISH_STATUS_RE.search(stdout)
    if not m:
        return ("unknown", "")
    return (m.group(1), (m.group(2) or "").strip())


# ── 退出码 → 异常映射 ────────────────────────────────────────


def map_exit_code_to_exception(
    exit_code: int,
    stdout: str,
    stderr: str,
    *,
    platform: str,
    account_id: str,
) -> None:
    """subprocess 退出码 → 抛对应异常（或返回 ok）。

    - EXIT_OK → 静默通过
    - EXIT_LOGIN_EXPIRED → LoginExpired（编排层停止该平台）
    - EXIT_BAD_BUNDLE → PublishError（输入数据问题）
    - EXIT_PLATFORM_ERROR → PublishError（平台侧问题）
    - 其他 → PublishError（未知错误）
    """
    if exit_code == EXIT_OK:
        return
    snippet = (stderr or stdout)[-400:]
    if exit_code == EXIT_LOGIN_EXPIRED:
        raise LoginExpired(
            f"{platform}/{account_id} cookie/login expired: {snippet}"
        )
    if exit_code == EXIT_BAD_BUNDLE:
        raise PublishError(
            f"{platform}/{account_id} bad bundle (exit={exit_code}): "
            f"{snippet}"
        )
    # EXIT_PLATFORM_ERROR + 其它
    raise PublishError(
        f"{platform}/{account_id} publish failed "
        f"(exit={exit_code}): {snippet}"
    )


# ── runner 抽象（subprocess.run 注入） ─────────────────────


def _real_subprocess_runner(
    cmd: list[str],
    *,
    timeout_s: float,
    cwd: str | None = None,
) -> tuple[int, str, str]:
    """生产 runner：subprocess.run → (returncode, stdout, stderr)。

    TimeoutExpired → PublishError（超时 = 平台卡住，让编排层决定）。
    """
    import subprocess  # 局部 import，CI 无 subprocess 也能 import 此模块
    try:
        completed = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout_s, cwd=cwd,
        )
    except subprocess.TimeoutExpired as e:
        raise PublishError(
            f"{PLATFORM} publish timeout after {timeout_s}s"
        ) from e
    except FileNotFoundError as e:
        # 命令不存在（CLI 没装 / 路径错）
        raise PublishError(
            f"{PLATFORM} CLI not found: {cmd[0] if cmd else '?'}: {e}"
        ) from e
    return (completed.returncode, completed.stdout, completed.stderr)


# ── XiaohongshuPublisher ──────────────────────────────────


class XiaohongshuPublisher(PublisherAdapter):
    """小红书 (xiaohongshu.com) PublisherAdapter。"""

    platform = PLATFORM

    def __init__(
        self,
        *,
        # 凭据路径（XiaohongshuSkills 自管 cookie/state；我们只记录路径）
        cookies_path: Path,
        # XiaohongshuSkills 安装根目录（CLI 在 <skills>/scripts/main.ts）
        skills_path: str | Path | None = None,
        # 注入：测试 fake / 生产真实 subprocess
        runner: Callable[..., tuple[int, str, str]] | None = None,
        # CLI 超时
        timeout_s: float = 600.0,
    ) -> None:
        if not cookies_path:
            raise ValueError("XiaohongshuPublisher requires cookies_path")
        self._cookies = Path(cookies_path)
        self._skills = Path(
            skills_path
            or _resolve_skills_path_from_env()
            or DEFAULT_SKILLS_PATH,
        ).expanduser()
        self._runner = runner or _real_subprocess_runner
        self._timeout = timeout_s

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

        # 2. slides JSON 结构
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
            tags_text = tags_p.read_text(encoding="utf-8")
        except OSError as e:
            issues.append(f"tags.txt unreadable: {e}")
            return issues
        tags = _parse_tags(tags_text)
        if not (TAG_MIN_COUNT <= len(tags) <= TAG_MAX_COUNT):
            issues.append(
                f"tags count {len(tags)} out of range "
                f"[{TAG_MIN_COUNT}, {TAG_MAX_COUNT}]"
            )

        # 5. cookies 存在性
        if not self._cookies.exists():
            issues.append(
                f"cookies/state file missing: {self._cookies} "
                f"(run `python -m pipeline.run login xiaohongshu <account>`)"
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

        # dry-run：不调 subprocess，直接返回模拟结果
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

        # 拼装 CLI 命令（evaluation-notes §2 护栏 2：固定 path，不追 HEAD）
        cli_script = self._skills / "scripts" / "main.ts"
        if not cli_script.exists():
            raise PublishError(
                f"{PLATFORM} CLI script not found: {cli_script}. "
                f"Set XHS_SKILLS_PATH or config.platforms.xiaohongshu.skills_path"
            )

        cmd = [
            "npx", "-y", "bun", str(cli_script),
            "--title", bundle.title,
            "--slides", str(slides_p),
            "--caption", str(caption_p),
            "--tags", str(tags_p),
            "--cookies", str(self._cookies),
            "--account", account.id,
            "--json",  # 强制 JSON 出口（便于解析）
        ]

        exit_code, stdout, stderr = self._runner(
            cmd, timeout_s=self._timeout,
        )

        # 退出码 → 异常
        map_exit_code_to_exception(
            exit_code, stdout, stderr,
            platform=PLATFORM, account_id=account.id,
        )

        # 解析 PUBLISH_STATUS 行
        state, detail = parse_publish_status(stdout)
        if state == "partial":
            # 部分成功：按 X 一样返回 PublishError 让编排层记 partial
            raise PublishError(
                f"{PLATFORM}/{account.id} partial publish: {detail}; "
                "manual cleanup may be needed"
            )
        if state == "failed":
            raise PublishError(
                f"{PLATFORM}/{account.id} publish state=failed: {detail}; "
                f"stdout-tail: {stdout[-200:]!r}"
            )
        # state ∈ {ok, unknown}：EXIT_OK + 解析 JSON
        # （unknown = CLI 没发状态行但退出码 0，按成功处理；JSON 拿不到 post_id 也 OK）
        post_id, url = _extract_post_result(stdout)
        return PublishResult(
            platform_post_id=post_id,
            url=url,
            raw_response=stdout[-4000:],  # 截断防爆
        )


# ── helpers ────────────────────────────────────────────────


def _extract_post_result(stdout: str) -> tuple[str | None, str | None]:
    """从 JSON 出口 stdout 提取 (post_id, url)。

    实际 XiaohongshuSkills 的 --json 出口字段（evaluation-notes 未明确，
    待 vendor 集成时复核 HEAD CLI 签名）。本实现做启发式：
    - 整体 stdout 是 JSON → 找 savedPost/postId/url 字段
    - 不是 JSON → 返回 (None, None)
    """
    # 启发式 1：尝试整体 parse JSON
    try:
        data = json.loads(stdout)
    except ValueError:
        return (None, None)
    if not isinstance(data, dict):
        return (None, None)
    post_id = (
        data.get("postId")
        or data.get("post_id")
        or data.get("noteId")
        or data.get("id")
    )
    url = data.get("url") or data.get("noteUrl") or data.get("link")
    if post_id is not None and not isinstance(post_id, str):
        post_id = str(post_id)
    if url is not None and not isinstance(url, str):
        url = str(url)
    return (post_id, url)


def _resolve_skills_path_from_env() -> str | None:
    """XHS_SKILLS_PATH 环境变量解析（供 vendor 路径覆盖）。"""
    import os
    return os.environ.get("XHS_SKILLS_PATH")


__all__ = [
    "XiaohongshuPublisher",
    "parse_publish_status",
    "map_exit_code_to_exception",
    "EXIT_OK",
    "EXIT_LOGIN_EXPIRED",
    "EXIT_BAD_BUNDLE",
    "EXIT_PLATFORM_ERROR",
    "DEFAULT_SKILLS_PATH",
]