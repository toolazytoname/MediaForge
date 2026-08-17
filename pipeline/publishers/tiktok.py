"""TikTok Content Posting API (Direct Post / Inbox Upload).

User-context OAuth with ``video.publish`` is required. Missing user-context
fail-closes direct publish. Unreviewed clients may only use Inbox Upload
(user continues editing in the TikTok app) and must not claim public Direct Post.
Success requires a platform ``publish_id``; unknown receipts fail.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urlparse

from pipeline.publishers.base import (
    AccountConfig,
    LoginExpired,
    PostBundle,
    PublishError,
    PublishResult,
    PublisherAdapter,
)
from pipeline.publishers.capabilities import AdapterCapabilities, default_capabilities

TIKTOK_API_BASE = "https://open.tiktokapis.com"
TIKTOK_AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"
TIKTOK_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
INBOX_INIT_PATH = "/v2/post/publish/inbox/video/init/"
DIRECT_INIT_PATH = "/v2/post/publish/video/init/"
PHOTO_INIT_PATH = "/v2/post/publish/content/init/"
STATUS_PATH = "/v2/post/publish/status/fetch/"
CANCEL_PATH = "/v2/post/publish/cancel/"
SCOPE_PUBLISH = "video.publish"
REQUIRED_DIRECT_SCOPES = frozenset({SCOPE_PUBLISH})
VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".mkv"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
PUBLIC_PRIVACY = frozenset({"public", "public_to_everyone", "everyone"})
PRIVATE_PRIVACY = frozenset({"private", "self", "self_only", "sandbox"})


@dataclass(frozen=True)
class TikTokCredentials:
    access_token: str
    open_id: str | None = None
    scopes: tuple[str, ...] = ()
    app_reviewed: bool = False
    client_key: str | None = None

    @property
    def has_user_context(self) -> bool:
        if not self.access_token or not self.open_id:
            return False
        have = {scope.lower() for scope in self.scopes}
        return SCOPE_PUBLISH in have

    @property
    def can_direct_post(self) -> bool:
        return self.has_user_context and self.app_reviewed

    @property
    def can_publish_public(self) -> bool:
        return self.can_direct_post


def _parse_scopes(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        return tuple(part for part in raw.replace(",", " ").split() if part)
    if isinstance(raw, (list, tuple)):
        return tuple(str(item) for item in raw if str(item).strip())
    return ()


def load_tiktok_credential_set(path: str | Path) -> TikTokCredentials:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"tiktok credentials file not found: {p}")
    raw = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"tiktok credentials at {p} must be a JSON object")
    token = raw.get("access_token") or raw.get("user_access_token")
    if not isinstance(token, str) or not token:
        return TikTokCredentials(access_token="")
    open_id = raw.get("open_id") or raw.get("user_id")
    if open_id is not None:
        open_id = str(open_id).strip() or None
    client_key = raw.get("client_key") or raw.get("client_id")
    if client_key is not None:
        client_key = str(client_key).strip() or None
    return TikTokCredentials(
        access_token=token,
        open_id=open_id,
        scopes=_parse_scopes(raw.get("scopes") or raw.get("scope")),
        app_reviewed=bool(raw.get("app_reviewed") or raw.get("has_app_review")),
        client_key=client_key,
    )


def _httpx_json(
    url: str,
    *,
    headers: dict,
    body: dict | None = None,
    timeout: float = 30.0,
) -> dict:
    import httpx
    try:
        resp = httpx.post(url, headers=headers, json=body, timeout=timeout)
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        raise PublishError(f"tiktok API network error: {exc!r}") from exc
    return _parse_http_response(resp, url)


def _httpx_upload(
    url: str,
    *,
    headers: dict,
    file_path: Path,
    timeout: float = 180.0,
) -> dict:
    import httpx
    try:
        data = file_path.read_bytes()
        put_headers = dict(headers)
        put_headers.setdefault("Content-Type", "video/mp4")
        put_headers.setdefault("Content-Length", str(len(data)))
        resp = httpx.put(url, headers=put_headers, content=data, timeout=timeout)
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        raise PublishError(f"tiktok upload network error: {exc!r}") from exc
    if getattr(resp, "status_code", 0) in (200, 201, 204) and not (getattr(resp, "text", "") or "").strip():
        return {"uploaded": True}
    return _parse_http_response(resp, url)


def _parse_http_response(resp: object, url: str) -> dict:
    status = getattr(resp, "status_code", 0)
    text = getattr(resp, "text", "") or ""
    if status in (401, 403):
        raise LoginExpired(f"tiktok API auth failed ({status}) at {url}")
    if status >= 400:
        raise PublishError(f"tiktok API HTTP {status} at {url}: {text[:300]}")
    if not text:
        return {}
    try:
        data = resp.json()  # type: ignore[union-attr]
    except ValueError as exc:
        raise PublishError(f"tiktok API non-JSON at {url}: {text[:300]!r}") from exc
    if not isinstance(data, dict):
        raise PublishError(f"tiktok API bad response shape at {url}")
    error = data.get("error")
    if isinstance(error, dict):
        code = str(error.get("code") or "").lower()
        if code and code not in {"ok", "0", "success"}:
            raise PublishError(f"tiktok API error {error.get('code')}: {error.get('message') or error}")
    return data


def _nested(payload: dict, key: str) -> object:
    data = payload.get("data")
    if isinstance(data, dict) and key in data:
        return data.get(key)
    return payload.get(key)


def _requested_privacy(bundle: PostBundle) -> str:
    extra = bundle.extra or {}
    raw = extra.get("privacy_level") or extra.get("visibility") or "private"
    value = str(raw).strip().lower()
    if value in PUBLIC_PRIVACY:
        return "PUBLIC_TO_EVERYONE"
    if value in {"friends", "mutual_follow_friends"}:
        return "MUTUAL_FOLLOW_FRIENDS"
    if value in {"followers", "follower_of_creator"}:
        return "FOLLOWER_OF_CREATOR"
    return "SELF_ONLY"


def _media_kind(paths: tuple[Path, ...]) -> str:
    if not paths:
        raise PublishError("tiktok publish requires a video or image file")
    suffixes = {path.suffix.lower() for path in paths}
    if suffixes <= IMAGE_SUFFIXES:
        return "photo"
    if any(path.suffix.lower() in VIDEO_SUFFIXES for path in paths):
        return "video"
    raise PublishError(f"unsupported tiktok media types: {sorted(suffixes)}")


class TikTokPublisher(PublisherAdapter):
    platform = "tiktok"

    def __init__(
        self,
        *,
        credentials: TikTokCredentials | None = None,
        access_token: str = "",
        open_id: str | None = None,
        scopes: Iterable[str] = (),
        app_reviewed: bool = False,
        http_post: Callable[..., dict] | None = None,
        http_upload: Callable[..., dict] | None = None,
        api_base: str = TIKTOK_API_BASE,
    ) -> None:
        if credentials is not None:
            self._creds = credentials
        else:
            self._creds = TikTokCredentials(
                access_token=access_token,
                open_id=open_id,
                scopes=tuple(scopes),
                app_reviewed=app_reviewed,
            )
        self._post = http_post or _httpx_json
        self._upload = http_upload or _httpx_upload
        self._api = api_base.rstrip("/")

    def capabilities(self) -> AdapterCapabilities:
        if not self._creds.has_user_context:
            detail = (
                "TikTok Content Posting API fail-closed: need user access_token "
                "+ open_id + video.publish. Public direct is not claimed."
            )
        elif not self._creds.can_direct_post:
            detail = (
                "TikTok user-context present; unreviewed client may only Inbox Upload. "
                "User continues editing in the TikTok app. Public Direct Post is not supported."
            )
        else:
            detail = "TikTok app review complete; Direct Post may request a public privacy level"
        return default_capabilities(direct=self._creds.has_user_context, detail=detail)

    def validate(self, bundle: PostBundle) -> list[str]:
        issues: list[str] = []
        if not (bundle.title or "").strip() and not (bundle.extra or {}).get("description"):
            issues.append("title is empty")
        if not bundle.media_paths:
            issues.append("no video or image file provided")
        else:
            try:
                kind = _media_kind(tuple(Path(path) for path in bundle.media_paths))
            except PublishError as exc:
                issues.append(str(exc))
                return issues
            for path in bundle.media_paths:
                media = Path(path)
                if not media.exists():
                    issues.append(f"media file missing: {media}")
                elif media.stat().st_size == 0:
                    issues.append(f"media file is empty: {media}")
            if kind == "photo" and not self._creds.can_direct_post:
                issues.append(
                    "TikTok photo Direct Post requires completed app review; "
                    "Inbox Upload is video-only. Public direct is not claimed."
                )
        privacy = _requested_privacy(bundle)
        if privacy == "PUBLIC_TO_EVERYONE" and not self._creds.can_publish_public:
            issues.append("public Direct Post requires completed TikTok app review")
        if not self._creds.has_user_context:
            issues.append(
                "missing user-context OAuth (open_id + video.publish); direct is unavailable"
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
        privacy = _requested_privacy(bundle)
        if dry_run:
            mode = "direct" if self._creds.can_direct_post else "inbox"
            return PublishResult(
                platform_post_id=f"dry-tiktok-{kind}-{mode}",
                url=None,
                raw_response=json.dumps({
                    "dry_run": True,
                    "platform": "tiktok",
                    "account": account.id,
                    "kind": kind,
                    "mode": mode,
                    "privacy_level": privacy,
                    "has_user_context": self._creds.has_user_context,
                    "app_reviewed": self._creds.app_reviewed,
                    "public_direct_claimed": False,
                }, ensure_ascii=False),
            )
        if not self._creds.has_user_context:
            raise PublishError(
                "TikTok publish is disabled: missing verifiable user-context OAuth "
                "(open_id + scope video.publish). Public direct is not claimed."
            )
        if privacy == "PUBLIC_TO_EVERYONE" and not self._creds.can_publish_public:
            raise PublishError(
                "TikTok public Direct Post is not supported until Content Posting API "
                "app review completes. Unreviewed clients may only Inbox Upload "
                "(user continues editing in the TikTok app)."
            )
        if kind == "photo":
            if not self._creds.can_direct_post:
                raise PublishError(
                    "TikTok photo Direct Post requires completed app review. "
                    "Inbox Upload is video-only. Public direct is not claimed."
                )
            return self._direct_photo(media, bundle, account, privacy)
        if not self._creds.can_direct_post:
            return self._inbox_video(media[0], account)
        return self._direct_video(media[0], bundle, account, privacy)

    def compensate(self, platform_post_id: str) -> dict:
        if not platform_post_id:
            raise PublishError("cannot compensate tiktok post without publish_id")
        if not self._creds.has_user_context:
            raise PublishError("tiktok compensate requires user-context OAuth")
        data = self._post(
            f"{self._api}{CANCEL_PATH}",
            headers=self._headers(),
            body={"publish_id": platform_post_id},
            timeout=30.0,
        )
        return {"deleted": platform_post_id, "raw": data}

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._creds.access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        }

    def _inbox_video(self, video: Path, account: AccountConfig) -> PublishResult:
        size = video.stat().st_size
        init = self._post(
            f"{self._api}{INBOX_INIT_PATH}",
            headers=self._headers(),
            body={
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": size,
                    "chunk_size": size,
                    "total_chunk_count": 1,
                }
            },
            timeout=30.0,
        )
        return self._finish_upload(init, video, account, mode="inbox", privacy="SELF_ONLY")

    def _direct_video(
        self, video: Path, bundle: PostBundle, account: AccountConfig, privacy: str,
    ) -> PublishResult:
        size = video.stat().st_size
        init = self._post(
            f"{self._api}{DIRECT_INIT_PATH}",
            headers=self._headers(),
            body={
                "post_info": {
                    "title": bundle.title,
                    "privacy_level": privacy,
                },
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": size,
                    "chunk_size": size,
                    "total_chunk_count": 1,
                },
            },
            timeout=30.0,
        )
        return self._finish_upload(init, video, account, mode="direct", privacy=privacy)

    def _direct_photo(
        self,
        images: tuple[Path, ...],
        bundle: PostBundle,
        account: AccountConfig,
        privacy: str,
    ) -> PublishResult:
        media_url = str((bundle.extra or {}).get("media_url") or "").strip()
        if urlparse(media_url).scheme != "https":
            raise PublishError(
                "TikTok photo Direct Post requires a publicly fetchable HTTPS media URL"
            )
        init = self._post(
            f"{self._api}{PHOTO_INIT_PATH}",
            headers=self._headers(),
            body={
                "post_info": {
                    "title": bundle.title,
                    "privacy_level": privacy,
                    "description": (bundle.extra or {}).get("description") or bundle.title,
                },
                "source_info": {
                    "source": "PULL_FROM_URL",
                    "photo_cover_index": 0,
                    "photo_images": [media_url],
                },
                "post_mode": "DIRECT_POST",
                "media_type": "PHOTO",
            },
            timeout=30.0,
        )
        publish_id = _require_publish_id(init)
        return PublishResult(
            platform_post_id=publish_id,
            url=None,
            raw_response=json.dumps({
                "platform": "tiktok",
                "account": account.id,
                "kind": "photo",
                "mode": "direct",
                "publish_id": publish_id,
                "privacy_level": privacy,
            }, ensure_ascii=False),
        )

    def _finish_upload(
        self,
        init: dict,
        video: Path,
        account: AccountConfig,
        *,
        mode: str,
        privacy: str,
    ) -> PublishResult:
        publish_id = _require_publish_id(init)
        upload_url = _nested(init, "upload_url")
        if not isinstance(upload_url, str) or not upload_url.strip():
            raise PublishError("tiktok init returned no upload_url; unknown receipt is failure")
        self._upload(
            upload_url,
            headers={"Authorization": f"Bearer {self._creds.access_token}"},
            file_path=video,
            timeout=180.0,
        )
        return PublishResult(
            platform_post_id=publish_id,
            url=None,
            raw_response=json.dumps({
                "platform": "tiktok",
                "account": account.id,
                "kind": "video",
                "mode": mode,
                "publish_id": publish_id,
                "privacy_level": privacy,
                "user_continues_in_app": mode == "inbox",
                "public_direct_claimed": False,
            }, ensure_ascii=False),
        )


def _require_publish_id(payload: dict) -> str:
    publish_id = _nested(payload, "publish_id")
    if not isinstance(publish_id, str) or not publish_id.strip():
        raise PublishError("tiktok init returned no publish_id; unknown receipt is failure")
    return publish_id.strip()


__all__ = [
    "REQUIRED_DIRECT_SCOPES",
    "SCOPE_PUBLISH",
    "TIKTOK_API_BASE",
    "TIKTOK_AUTHORIZE_URL",
    "TIKTOK_TOKEN_URL",
    "TikTokCredentials",
    "TikTokPublisher",
    "load_tiktok_credential_set",
]
