"""Send a Project WeChat variant into the official draft box.

This path only calls token + image upload + draft/add. It never enables
mass send, never writes publish.enabled, and never calls freepublish.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pipeline import projects as project_store
from pipeline import variants as variant_store
from pipeline import visuals
from pipeline.creators.wechat_html import markdown_to_wechat_html
from pipeline.publishers.base import PublishError
from pipeline.publishers.wechat_mp import (
    DIGEST_MAX_LEN,
    TITLE_MAX_LEN,
    WechatMpPublisher,
    load_wechat_credentials,
)


DEFAULT_CREDENTIALS = Path("secrets/wechat_mp_main.json")
_RECEIPT_NAME = "wechat_draft.json"
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


class WechatDraftError(ValueError):
    """The project is not ready, or WeChat refused the draft."""


@dataclass(frozen=True)
class WechatDraftReceipt:
    project_id: str
    title: str
    media_id: str
    destination: str
    published: bool
    sent_at: str
    message: str


def send_project_wechat_draft(
    project_id: str,
    *,
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
    credentials_path: str | Path = DEFAULT_CREDENTIALS,
    publisher: WechatMpPublisher | None = None,
    now: str | None = None,
) -> WechatDraftReceipt:
    project_store.load_project(project_id, projects_root=projects_root)
    variant = _wechat_variant(project_id, projects_root)
    cover = _cover_path(project_id, variant, projects_root)
    title = _clip(variant.title, TITLE_MAX_LEN, "标题")
    digest = _clip(variant.summary or _fallback_digest(variant.body), DIGEST_MAX_LEN, "摘要")
    client = publisher or _live_publisher(credentials_path)
    try:
        article_md = _replace_local_images(variant.body, client, projects_root)
        html_body = markdown_to_wechat_html(article_md)
        thumb_media_id = client._upload_cover(cover)
        response = client._upload_draft(
            title=title, digest=digest, html_body=html_body, thumb_media_id=thumb_media_id,
        )
    except PublishError as error:
        raise WechatDraftError(str(error)) from error
    media_id = response.get("media_id")
    if not isinstance(media_id, str) or not media_id.strip():
        raise WechatDraftError(f"wechat draft/add missing media_id: {response!r}")
    receipt = WechatDraftReceipt(
        project_id=project_id,
        title=title,
        media_id=media_id.strip(),
        destination="wechat_draft",
        published=False,
        sent_at=now or datetime.now(timezone.utc).isoformat(),
        message="已送进公众号草稿箱，不会群发。请到微信公众平台 → 草稿箱核对。",
    )
    _write_receipt(project_id, receipt, projects_root)
    return receipt


def load_receipt(
    project_id: str, *, projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
) -> WechatDraftReceipt | None:
    path = Path(projects_root) / project_id / _RECEIPT_NAME
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return WechatDraftReceipt(**payload)


def _live_publisher(credentials_path: str | Path) -> WechatMpPublisher:
    path = Path(credentials_path)
    if not path.exists():
        raise WechatDraftError("还没有保存公众号 AppID 和 AppSecret。")
    app_id, app_secret = load_wechat_credentials(path)
    return WechatMpPublisher(app_id=app_id, app_secret=app_secret)


def _wechat_variant(project_id: str, projects_root: str | Path):
    try:
        board = variant_store.load_variants(project_id, projects_root=projects_root)
    except variant_store.VariantsError as error:
        raise WechatDraftError(str(error)) from error
    variant = next((item for item in board.variants if item.platform == "wechat_mp"), None)
    if variant is None or not variant.body.strip():
        raise WechatDraftError("还没有微信稿。先在文章页生成公众号版本。")
    return variant


def _cover_path(project_id: str, variant: Any, projects_root: str | Path) -> Path:
    plan = visuals.load_visuals(project_id, projects_root=projects_root)
    purpose = {slot.id: slot.purpose for slot in plan.slots}
    selected = [
        asset for asset in plan.assets
        if asset.status == "selected" and asset.file_path
    ]
    cover = next((asset for asset in selected if "封面" in purpose.get(asset.slot_id, "")), None)
    if cover is None or not cover.file_path:
        raise WechatDraftError("还没有封面图。公众号草稿必须有一张封面。")
    path = Path(projects_root) / project_id / cover.file_path
    if not path.is_file():
        raise WechatDraftError(f"封面文件不存在：{cover.file_path}")
    return path


def _replace_local_images(markdown: str, client: WechatMpPublisher, projects_root: str | Path) -> str:
    def replace(match: re.Match[str]) -> str:
        caption, raw = match.group(1), match.group(2).strip()
        local = _resolve_local_image(raw, projects_root)
        if local is None:
            return match.group(0)
        if not local.is_file():
            raise WechatDraftError(f"正文图片不存在：{raw}")
        try:
            cdn = client._upload_content_image(local)
        except PublishError as error:
            raise WechatDraftError(str(error)) from error
        return f"![{caption}]({cdn})"

    return _IMAGE_RE.sub(replace, markdown)


def _resolve_local_image(reference: str, projects_root: str | Path) -> Path | None:
    cleaned = reference.split("?", 1)[0]
    if cleaned.startswith("http://") or cleaned.startswith("https://"):
        return None
    if cleaned.startswith("/output/projects/"):
        relative = cleaned[len("/output/projects/"):]
        return Path(projects_root) / relative
    if cleaned.startswith("output/projects/"):
        return Path(projects_root) / cleaned[len("output/projects/"):]
    if cleaned.startswith("assets/"):
        return Path(projects_root) / cleaned
    return None


def _clip(value: str, limit: int, label: str) -> str:
    text = (value or "").strip()
    if not text:
        raise WechatDraftError(f"微信稿缺少{label}")
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _fallback_digest(body: str) -> str:
    plain = re.sub(r"!\[.*?\]\([^)]+\)", "", body)
    plain = re.sub(r"[#*_>`]", "", plain)
    return " ".join(plain.split())


def _write_receipt(project_id: str, receipt: WechatDraftReceipt, projects_root: str | Path) -> None:
    path = Path(projects_root) / project_id / _RECEIPT_NAME
    path.write_text(json.dumps(asdict(receipt), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
