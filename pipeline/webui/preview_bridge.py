"""M10-12 UI 发布 dry-run 预演桥接层。

本模块只做预演：
  - 用真实 PublisherAdapter.validate() 检查平台产物；
  - 调 safe_publish(..., dry_run=True) 走发布三重锁与 INTENT 日志；
  - safe_publish 在内存 DB 副本上执行，真实 state.db 不发生状态转移；
  - 传给 safe_publish 的 adapter 是防真发包装器，绝不触达原始 adapter.publish。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from pipeline import db
from pipeline.models import Publication, PublicationStatus
from pipeline.publishers import get_adapter, safe_publish
from pipeline.publishers.base import (
    AccountConfig,
    PostBundle,
    PublishError,
    PublishResult,
    PublisherAdapter,
)


# ── errors ───────────────────────────────────────────────────


class PreviewError(Exception):
    """发布预演错误基类。"""


class PublicationNotFoundError(PreviewError):
    """publication_id 不存在。"""


class PublicationWrongStatusError(PreviewError):
    """publication 不是 queued，不能预演发布。"""


class ConfigLoadError(PreviewError):
    """config.yaml 加载失败。"""


class PlatformNotConfiguredError(PreviewError):
    """cfg.platforms 中没有对应 platform。"""


class AccountNotFoundError(PreviewError):
    """publication.account_id 不在平台账号配置中。"""


class AdapterInitError(PreviewError):
    """真实 PublisherAdapter 初始化失败。"""


class ContentNotFoundError(PreviewError):
    """publication.content_id 指向的 content 不存在。"""


# ── bundle 构造 ───────────────────────────────────────────────


def _build_preview_bundle(
    conn: sqlite3.Connection,
    pub: Publication,
) -> PostBundle:
    """从 publication + content 构造预演用 PostBundle。

    纯函数语义：只读 DB 与产物文件，不修改 DB / 文件系统。body_path 保持为
    canonical.md，兼容现有 X / 小红书 adapter 的 resolver：它们会从
    body_path.parent 找平台派生产物目录。
    """
    content = db.get_content(conn, pub.content_id)
    if content is None:
        raise ContentNotFoundError(
            f"content not found for publication {pub.id}: {pub.content_id}"
        )

    body_path = Path(content.canonical_path)
    content_dir = body_path.parent
    common_media = _existing_paths([
        Path(content.cover_path) if content.cover_path else None,
        *[Path(p) for p in content.inline_images],
    ])

    if pub.platform == "xiaohongshu":
        xhs_dir = content_dir / "xiaohongshu"
        caption_path = xhs_dir / "caption.md"
        tags_path = xhs_dir / "tags.txt"
        media_paths = _existing_paths([
            *common_media,
            xhs_dir / "cover.png",
            *sorted(xhs_dir.glob("card-*.png")),
            *sorted((xhs_dir / "images").glob("*.png")),
        ])
        return PostBundle(
            content_id=content.id,
            title=content.title,
            body_path=body_path,
            media_paths=tuple(media_paths),
            tags=tuple(_read_xhs_tags(tags_path)),
            extra={
                "platform": pub.platform,
                "publication_id": pub.id,
                "body_preview_path": str(caption_path),
            },
        )

    if pub.platform == "x":
        thread_path = content_dir / "x" / "thread.md"
        return PostBundle(
            content_id=content.id,
            title=content.title,
            body_path=thread_path,
            media_paths=tuple(common_media),
            tags=(),
            extra={
                "platform": pub.platform,
                "publication_id": pub.id,
                "body_preview_path": str(thread_path),
            },
        )

    if pub.platform == "toutiao":
        toutiao_path = content_dir / "toutiao.md"
        return PostBundle(
            content_id=content.id,
            title=content.title,
            body_path=body_path,
            media_paths=tuple(common_media),
            tags=(),
            extra={
                "platform": pub.platform,
                "publication_id": pub.id,
                "body_preview_path": str(toutiao_path),
            },
        )

    if pub.platform == "douyin":
        media_paths = tuple(common_media) or tuple(sorted(content_dir.glob("*.mp4")))
        return PostBundle(
            content_id=content.id,
            title=content.title,
            body_path=body_path,
            media_paths=media_paths,
            tags=(),
            extra={
                "platform": pub.platform,
                "publication_id": pub.id,
            },
        )

    return PostBundle(
        content_id=content.id,
        title=content.title,
        body_path=body_path,
        media_paths=tuple(common_media),
        tags=(),
        extra={"platform": pub.platform, "publication_id": pub.id},
    )


def _existing_paths(paths: list[Path | None]) -> list[Path]:
    """保序去重，仅返回存在的路径。"""
    seen: set[str] = set()
    out: list[Path] = []
    for path in paths:
        if path is None or not path.exists():
            continue
        key = str(path)
        if key in seen:
            continue
        seen = {*seen, key}
        out = [*out, path]
    return out


def _read_xhs_tags(path: Path) -> list[str]:
    """读取小红书 tags.txt；文件不存在时返回空列表。"""
    if not path.exists():
        return []
    try:
        from pipeline.publishers.xiaohongshu import _parse_tags
        return _parse_tags(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise PublishError(f"cannot read xiaohongshu tags: {e}") from e


# ── 预演执行 ─────────────────────────────────────────────────


def _run_preview(
    conn: sqlite3.Connection,
    publication_id: str,
    run_id: str,
    now: str,
) -> dict[str, Any]:
    """执行单条 publication 预演，返回可序列化结果。"""
    pub = db.get_publication(conn, publication_id)
    if pub is None:
        raise PublicationNotFoundError(f"publication not found: {publication_id}")
    if pub.status != PublicationStatus.QUEUED.value:
        raise PublicationWrongStatusError(
            f"publication {publication_id} status is {pub.status!r}, not queued"
        )

    from pipeline.webui import deps

    cfg, err = deps.get_config()
    if err:
        raise ConfigLoadError(str(err))

    plat_obj = getattr(cfg.platforms, pub.platform, None)
    if plat_obj is None:
        raise PlatformNotConfiguredError(
            f"platform not configured: {pub.platform}"
        )

    account_cfg = next(
        (account for account in plat_obj.accounts if account.id == pub.account_id),
        None,
    )
    if account_cfg is None:
        raise AccountNotFoundError(
            f"account {pub.account_id!r} not found for platform {pub.platform!r}"
        )

    account = AccountConfig(
        id=account_cfg.id,
        credentials_path=_account_credentials_path(account_cfg),
    )
    try:
        adapter = get_adapter(pub.platform, account=account, config=cfg)
    except Exception as e:
        raise AdapterInitError(str(e)) from e

    bundle = _build_preview_bundle(conn, pub)
    validate_errors = adapter.validate(bundle)

    preview_adapter = _NoPublishPreviewAdapter(
        adapter,
        validate_errors=tuple(validate_errors),
        run_id=run_id,
    )
    dry_conn = _clone_connection(conn)
    try:
        result = safe_publish(
            dry_conn,
            pub,
            preview_adapter,
            config=cfg.publish,
            account=account,
            dry_run=True,
            now_iso=now,
        )
    finally:
        dry_conn.close()

    return {
        "validate_passed": len(validate_errors) == 0,
        "validate_errors": list(validate_errors),
        "preview": {
            "title": bundle.title,
            "body_excerpt": _body_excerpt(bundle),
            "media": [str(path) for path in bundle.media_paths],
            "tags": list(bundle.tags),
            "platform": pub.platform,
            "account_id": pub.account_id,
            "scheduled_at": pub.scheduled_at,
        },
        "safe_publish_result": {
            "published": result.published,
            "reason": result.reason,
            # safe_publish 旧实现的配置拒绝分支 dry_run 默认 False；预演端点
            # 的契约以本次调用参数为准，必须向 UI 明确这是 dry-run。
            "dry_run": True,
        },
    }


def _account_credentials_path(account_cfg: Any) -> Path:
    """兼容 API credentials 与 Playwright cookies 账号字段。"""
    raw = (
        getattr(account_cfg, "credentials", None)
        or getattr(account_cfg, "cookies", None)
        or getattr(account_cfg, "cookie_path", None)
    )
    if not raw:
        raise AdapterInitError(
            f"account {getattr(account_cfg, 'id', '<unknown>')!r} has no credentials path"
        )
    return Path(raw)


def _clone_connection(conn: sqlite3.Connection) -> sqlite3.Connection:
    """复制当前 DB 到内存连接，供 safe_publish 写锁/INTENT 流程使用。"""
    clone = sqlite3.connect(":memory:")
    clone.row_factory = sqlite3.Row
    conn.backup(clone)
    return clone


def _body_excerpt(bundle: PostBundle, *, limit: int = 200) -> str:
    """优先展示平台正文，否则回退 canonical.md。"""
    candidate = bundle.extra.get("body_preview_path") if bundle.extra else None
    path = Path(candidate) if isinstance(candidate, str) else bundle.body_path
    if not path.exists():
        path = bundle.body_path
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")[:limit]
    except OSError:
        return ""


class _NoPublishPreviewAdapter(PublisherAdapter):
    """safe_publish 专用防真发 adapter 包装器。

    validate 返回真实 adapter.validate(bundle) 的结果；publish 只在 dry-run 参数为
    True 时返回本地模拟结果，永不调用真实 adapter.publish。
    """

    def __init__(
        self,
        adapter: PublisherAdapter,
        *,
        validate_errors: tuple[str, ...],
        run_id: str,
    ) -> None:
        self._adapter = adapter
        self._validate_errors = validate_errors
        self._run_id = run_id
        self.platform = adapter.platform

    def validate(self, bundle: PostBundle) -> list[str]:
        return list(self._validate_errors)

    def publish(
        self,
        bundle: PostBundle,
        account: AccountConfig,
        dry_run: bool,
    ) -> PublishResult:
        if dry_run is not True:
            raise PublishError("preview adapter refuses non-dry-run publish")
        return PublishResult(
            platform_post_id=f"dry-preview-{self._run_id}",
            url=None,
            raw_response=json.dumps({
                "dry_run": True,
                "platform": self.platform,
                "account": account.id,
                "content_id": bundle.content_id,
                "preview_only": True,
            }, ensure_ascii=False),
        )


__all__ = [
    "_build_preview_bundle",
    "_run_preview",
    "PreviewError",
    "PublicationNotFoundError",
    "PublicationWrongStatusError",
    "ConfigLoadError",
    "PlatformNotConfiguredError",
    "AccountNotFoundError",
    "AdapterInitError",
]
