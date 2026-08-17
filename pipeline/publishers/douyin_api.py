"""Douyin official content API (video.create + image text). Playwright is not the default.

User-context OAuth is required. Missing open_id / video.create scope fail-closes
direct publish. Success requires a platform item_id; unknown receipts fail.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from pipeline.publishers.base import (
    AccountConfig,
    LoginExpired,
    PostBundle,
    PublishError,
    PublishResult,
    PublisherAdapter,
)
from pipeline.publishers.capabilities import AdapterCapabilities, default_capabilities

DOUYIN_API_BASE = "https://open.douyin.com"
VIDEO_UPLOAD_PATH = "/video/upload/"
VIDEO_CREATE_PATH = "/video/create/"
IMAGE_UPLOAD_PATH = "/api/douyin/v1/video/upload_image/"
IMAGE_CREATE_PATH = "/api/douyin/v1/video/create_image_text/"
ITEM_DELETE_PATH = "/item/delete/"

# Official scope is video.create; newer apps may issue video.create.bind.
REQUIRED_DIRECT_SCOPES = frozenset({"video.create"})
BIND_SCOPE = "video.create.bind"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".webm"}
TITLE_MAX_LEN = 30
VIDEO_MAX_BYTES = 128 * 1024 * 1024


@dataclass(frozen=True)
class DouyinCredentials:
    access_token: str
    open_id: str | None = None
    scopes: tuple[str, ...] = ()
    client_key: str | None = None

    @property
    def has_user_context(self) -> bool:
        if not self.access_token or not self.open_id:
            return False
        have = {scope.lower() for scope in self.scopes}
        return bool(REQUIRED_DIRECT_SCOPES & have) or BIND_SCOPE in have


def _parse_scopes(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        return tuple(part for part in raw.replace(",", " ").split() if part)
    if isinstance(raw, (list, tuple)):
        return tuple(str(item) for item in raw if str(item).strip())
    return ()


def load_douyin_credential_set(path: str | Path) -> DouyinCredentials:
    """secrets/douyin_<account>.json → DouyinCredentials.

    Official path needs access_token + open_id + video.create (or video.create.bind).
    Playwright cookie dumps are accepted as files but yield no user-context.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"douyin credentials file not found: {p}")
    raw = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"douyin credentials at {p} must be a JSON object")
    token = raw.get("access_token") or raw.get("user_access_token")
    if not isinstance(token, str) or not token:
        # Cookie storage_state is not user-context OAuth.
        return DouyinCredentials(access_token="", open_id=None, scopes=())
    open_id = raw.get("open_id") or raw.get("user_id")
    if open_id is not None:
        open_id = str(open_id).strip() or None
    scopes = _parse_scopes(raw.get("scopes") or raw.get("scope"))
    client_key = raw.get("client_key") or raw.get("app_id")
    if client_key is not None:
        client_key = str(client_key).strip() or None
    return DouyinCredentials(
        access_token=token,
        open_id=open_id,
        scopes=scopes,
        client_key=client_key,
    )


def _httpx_json(
    url: str,
    *,
    headers: dict,
    params: dict | None = None,
    body: dict | None = None,
    timeout: float = 30.0,
) -> dict:
    import httpx
    try:
        resp = httpx.post(url, headers=headers, params=params, json=body, timeout=timeout)
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        raise PublishError(f"douyin API network error: {exc!r}") from exc
    return _parse_http_response(resp, url)


def _httpx_upload(
    url: str,
    *,
    headers: dict,
    params: dict | None = None,
    file_path: Path,
    field_name: str = "video",
    timeout: float = 120.0,
) -> dict:
    import httpx
    try:
        with file_path.open("rb") as handle:
            files = {field_name: (file_path.name, handle, "application/octet-stream")}
            resp = httpx.post(url, headers=headers, params=params, files=files, timeout=timeout)
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        raise PublishError(f"douyin upload network error: {exc!r}") from exc
    return _parse_http_response(resp, url)


def _parse_http_response(resp: object, url: str) -> dict:
    status = getattr(resp, "status_code", 0)
    text = getattr(resp, "text", "") or ""
    if status in (401, 403):
        raise LoginExpired(f"douyin API auth failed ({status}) at {url}")
    if status >= 400:
        raise PublishError(f"douyin API HTTP {status} at {url}: {text[:300]}")
    try:
        data = resp.json()  # type: ignore[union-attr]
    except ValueError as exc:
        raise PublishError(f"douyin API non-JSON at {url}: {text[:300]!r}") from exc
    if not isinstance(data, dict):
        raise PublishError(f"douyin API bad response shape at {url}")
    extra = data.get("extra") if isinstance(data.get("extra"), dict) else {}
    payload = data.get("data") if isinstance(data.get("data"), dict) else {}
    err = payload.get("error_code", extra.get("error_code", 0)) or 0
    if err:
        desc = payload.get("description") or extra.get("description") or ""
        raise PublishError(f"douyin API error {err}: {desc}")
    return data


def _media_kind(paths: tuple[Path, ...]) -> str:
    if not paths:
        raise PublishError("douyin official publish requires a video or image file")
    suffixes = {path.suffix.lower() for path in paths}
    if suffixes <= IMAGE_SUFFIXES:
        return "image"
    if any(path.suffix.lower() in VIDEO_SUFFIXES for path in paths):
        return "video"
    raise PublishError(f"unsupported douyin media types: {sorted(suffixes)}")


def _require_receipt(item_id: object, *, kind: str) -> str:
    if not isinstance(item_id, str) or not item_id.strip():
        raise PublishError(f"douyin {kind} succeeded without item_id; unknown receipt is failure")
    return item_id.strip()


class DouyinApiPublisher(PublisherAdapter):
    """Official Douyin OAuth adapter. Default delivery path."""

    platform = "douyin"

    def __init__(
        self,
        *,
        credentials: DouyinCredentials | None = None,
        access_token: str = "",
        open_id: str | None = None,
        scopes: Iterable[str] = (),
        http_post: Callable[..., dict] | None = None,
        http_upload: Callable[..., dict] | None = None,
        api_base: str = DOUYIN_API_BASE,
    ) -> None:
        if credentials is not None:
            self._creds = credentials
        else:
            self._creds = DouyinCredentials(
                access_token=access_token,
                open_id=open_id,
                scopes=tuple(scopes),
            )
        self._post = http_post or _httpx_json
        self._upload = http_upload or _httpx_upload
        self._api = api_base.rstrip("/")

    def capabilities(self) -> AdapterCapabilities:
        if self._creds.has_user_context:
            detail = "Douyin official API user-context present; video.create available"
        else:
            detail = (
                "Douyin official API fail-closed: need user access_token + open_id "
                "+ video.create (or video.create.bind). Playwright is assisted-only."
            )
        return default_capabilities(direct=self._creds.has_user_context, detail=detail)

    def validate(self, bundle: PostBundle) -> list[str]:
        issues: list[str] = []
        title = (bundle.title or "").strip()
        if not title:
            issues.append("title is empty")
        elif len(title) > TITLE_MAX_LEN:
            issues.append(f"title too long: {len(title)} chars (max {TITLE_MAX_LEN})")
        if not bundle.media_paths:
            issues.append("no video or image file provided")
            return issues
        try:
            kind = _media_kind(tuple(Path(path) for path in bundle.media_paths))
        except PublishError as exc:
            issues.append(str(exc))
            return issues
        for path in bundle.media_paths:
            media = Path(path)
            if not media.exists():
                issues.append(f"media file missing: {media}")
                continue
            size = media.stat().st_size
            if size == 0:
                issues.append(f"media file is empty: {media}")
            if kind == "video" and size > VIDEO_MAX_BYTES:
                issues.append(f"video too large: {size} bytes (max {VIDEO_MAX_BYTES})")
        if not self._creds.has_user_context:
            issues.append(
                "missing user-context OAuth (open_id + video.create); direct is unavailable"
            )
        return issues

    def publish(
        self,
        bundle: PostBundle,
        account: AccountConfig,
        dry_run: bool = False,
    ) -> PublishResult:
        media = tuple(Path(path) for path in bundle.media_paths)
        kind = _media_kind(media)
        if dry_run:
            return PublishResult(
                platform_post_id=f"dry-douyin-{kind}",
                url=None,
                raw_response=json.dumps({
                    "dry_run": True,
                    "platform": "douyin",
                    "account": account.id,
                    "kind": kind,
                    "has_user_context": self._creds.has_user_context,
                }, ensure_ascii=False),
            )
        if not self._creds.has_user_context:
            raise PublishError(
                "Douyin official publish is disabled: missing verifiable user-context "
                "OAuth (open_id + scopes video.create or video.create.bind). "
                "Capability 'direct' is unavailable. Playwright is not the default path."
            )
        text = (bundle.extra or {}).get("description") or bundle.title
        if kind == "image":
            return self._publish_images(media, str(text), account)
        return self._publish_video(media[0], str(text), account)

    def compensate(self, platform_post_id: str) -> dict:
        """Minimal delete contract for a previously created item_id."""
        if not platform_post_id:
            raise PublishError("cannot compensate douyin post without item_id")
        if not self._creds.has_user_context:
            raise PublishError("douyin compensate requires user-context OAuth")
        data = self._post(
            f"{self._api}{ITEM_DELETE_PATH}",
            headers=self._headers(),
            params={"open_id": self._creds.open_id},
            body={"item_id": platform_post_id},
            timeout=30.0,
        )
        return {"deleted": platform_post_id, "raw": data}

    def _headers(self) -> dict[str, str]:
        return {
            "access-token": self._creds.access_token,
            "Content-Type": "application/json",
        }

    def _upload_headers(self) -> dict[str, str]:
        return {"access-token": self._creds.access_token}

    def _publish_video(self, video_path: Path, text: str, account: AccountConfig) -> PublishResult:
        uploaded = self._upload(
            f"{self._api}{VIDEO_UPLOAD_PATH}",
            headers=self._upload_headers(),
            params={"open_id": self._creds.open_id},
            file_path=video_path,
            field_name="video",
            timeout=120.0,
        )
        video_id = _nested(uploaded, "video_id")
        if not isinstance(video_id, str) or not video_id:
            raise PublishError("douyin video upload returned no video_id; unknown receipt is failure")
        created = self._post(
            f"{self._api}{VIDEO_CREATE_PATH}",
            headers=self._headers(),
            params={"open_id": self._creds.open_id},
            body={"video_id": video_id, "text": text},
            timeout=30.0,
        )
        item_id = _require_receipt(_nested(created, "item_id"), kind="video.create")
        return PublishResult(
            platform_post_id=item_id,
            url=None,
            raw_response=json.dumps({
                "platform": "douyin",
                "account": account.id,
                "kind": "video",
                "item_id": item_id,
            }, ensure_ascii=False),
        )

    def _publish_images(
        self,
        images: tuple[Path, ...],
        text: str,
        account: AccountConfig,
    ) -> PublishResult:
        image_ids: list[str] = []
        for path in images:
            uploaded = self._upload(
                f"{self._api}{IMAGE_UPLOAD_PATH}",
                headers=self._upload_headers(),
                params={"open_id": self._creds.open_id},
                file_path=path,
                field_name="image",
                timeout=60.0,
            )
            image_id = _nested(uploaded, "image_id")
            if not isinstance(image_id, str) or not image_id:
                raise PublishError("douyin image upload returned no image_id; unknown receipt is failure")
            image_ids.append(image_id)
        created = self._post(
            f"{self._api}{IMAGE_CREATE_PATH}",
            headers=self._headers(),
            params={"open_id": self._creds.open_id},
            body={
                "image_list": [{"image_id": image_id} for image_id in image_ids],
                "text": text,
            },
            timeout=30.0,
        )
        item_id = _require_receipt(_nested(created, "item_id"), kind="create_image_text")
        return PublishResult(
            platform_post_id=item_id,
            url=None,
            raw_response=json.dumps({
                "platform": "douyin",
                "account": account.id,
                "kind": "image",
                "item_id": item_id,
                "image_count": len(image_ids),
            }, ensure_ascii=False),
        )


def _nested(payload: dict, key: str) -> object:
    data = payload.get("data")
    if isinstance(data, dict) and key in data:
        return data.get(key)
    return payload.get(key)


__all__ = [
    "BIND_SCOPE",
    "DOUYIN_API_BASE",
    "DouyinApiPublisher",
    "DouyinCredentials",
    "REQUIRED_DIRECT_SCOPES",
    "load_douyin_credential_set",
]
