"""YouTube official videos.insert adapter.

Without user-context OAuth, direct is fail-closed.
Without completed app review, only private/unlisted are allowed — never public.
Success requires a platform video id (and a watch URL derived from it).
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

YOUTUBE_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
SCOPE_UPLOAD = "https://www.googleapis.com/auth/youtube.upload"
SCOPE_UPLOAD_SHORT = "youtube.upload"
REQUIRED_DIRECT_SCOPES = frozenset({SCOPE_UPLOAD, SCOPE_UPLOAD_SHORT})
ALLOWED_VISIBILITY = frozenset({"private", "unlisted", "public"})
SAFE_VISIBILITY = frozenset({"private", "unlisted"})
VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".mkv"}


@dataclass(frozen=True)
class YoutubeCredentials:
    access_token: str
    user_id: str | None = None
    scopes: tuple[str, ...] = ()
    app_reviewed: bool = False

    @property
    def has_user_context(self) -> bool:
        if not self.access_token or not self.user_id:
            return False
        have = {scope.lower() for scope in self.scopes}
        return bool(
            SCOPE_UPLOAD.lower() in have
            or SCOPE_UPLOAD_SHORT in have
            or any(scope.endswith("youtube.upload") for scope in have)
        )

    @property
    def can_publish_public(self) -> bool:
        return self.has_user_context and self.app_reviewed


def _parse_scopes(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        return tuple(part for part in raw.replace(",", " ").split() if part)
    if isinstance(raw, (list, tuple)):
        return tuple(str(item) for item in raw if str(item).strip())
    return ()


def load_youtube_credential_set(path: str | Path) -> YoutubeCredentials:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"youtube credentials file not found: {p}")
    raw = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"youtube credentials at {p} must be a JSON object")
    token = raw.get("access_token") or raw.get("token")
    if not isinstance(token, str) or not token:
        raise ValueError(f"youtube credentials at {p} missing access_token")
    user_id = raw.get("user_id") or raw.get("channel_id")
    if user_id is not None:
        user_id = str(user_id).strip() or None
    return YoutubeCredentials(
        access_token=token,
        user_id=user_id,
        scopes=_parse_scopes(raw.get("scopes") or raw.get("scope")),
        app_reviewed=bool(raw.get("app_reviewed") or raw.get("has_app_review")),
    )


def _httpx_upload(
    url: str,
    *,
    headers: dict,
    params: dict | None = None,
    metadata: dict,
    file_path: Path,
    timeout: float = 180.0,
) -> dict:
    import httpx
    try:
        with file_path.open("rb") as handle:
            files = {
                "metadata": (
                    "metadata.json",
                    json.dumps(metadata).encode("utf-8"),
                    "application/json",
                ),
                "media": (file_path.name, handle, "video/mp4"),
            }
            resp = httpx.post(url, headers=headers, params=params, files=files, timeout=timeout)
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        raise PublishError(f"youtube upload network error: {exc!r}") from exc
    return _parse_http_response(resp, url)


def _httpx_delete(
    url: str,
    *,
    headers: dict,
    params: dict | None = None,
    timeout: float = 30.0,
) -> dict:
    import httpx
    try:
        resp = httpx.delete(url, headers=headers, params=params, timeout=timeout)
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        raise PublishError(f"youtube delete network error: {exc!r}") from exc
    if resp.status_code in (200, 204):
        return {"deleted": True, "status": resp.status_code}
    return _parse_http_response(resp, url)


def _parse_http_response(resp: object, url: str) -> dict:
    status = getattr(resp, "status_code", 0)
    text = getattr(resp, "text", "") or ""
    if status in (401, 403):
        raise LoginExpired(f"youtube API auth failed ({status}) at {url}")
    if status >= 400:
        raise PublishError(f"youtube API HTTP {status} at {url}: {text[:300]}")
    if not text:
        return {}
    try:
        data = resp.json()  # type: ignore[union-attr]
    except ValueError as exc:
        raise PublishError(f"youtube API non-JSON at {url}: {text[:300]!r}") from exc
    if not isinstance(data, dict):
        raise PublishError(f"youtube API bad response shape at {url}")
    error = data.get("error")
    if isinstance(error, dict):
        raise PublishError(f"youtube API error: {error.get('message') or error}")
    return data


def watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


class YoutubePublisher(PublisherAdapter):
    platform = "youtube"

    def __init__(
        self,
        *,
        credentials: YoutubeCredentials | None = None,
        access_token: str = "",
        user_id: str | None = None,
        scopes: Iterable[str] = (),
        app_reviewed: bool = False,
        http_upload: Callable[..., dict] | None = None,
        http_delete: Callable[..., dict] | None = None,
        upload_url: str = YOUTUBE_UPLOAD_URL,
        videos_url: str = YOUTUBE_VIDEOS_URL,
    ) -> None:
        if credentials is not None:
            self._creds = credentials
        else:
            if not access_token:
                raise ValueError("YoutubePublisher requires access_token")
            self._creds = YoutubeCredentials(
                access_token=access_token,
                user_id=user_id,
                scopes=tuple(scopes),
                app_reviewed=app_reviewed,
            )
        self._upload = http_upload or _httpx_upload
        self._delete = http_delete or _httpx_delete
        self._upload_url = upload_url
        self._videos_url = videos_url

    def capabilities(self) -> AdapterCapabilities:
        if not self._creds.has_user_context:
            detail = (
                "YouTube videos.insert disabled: need user OAuth "
                "(user_id + youtube.upload). Public publish is not claimed."
            )
        elif not self._creds.can_publish_public:
            detail = (
                "YouTube user-context present; only private/unlisted uploads "
                "until API app review completes. Public direct is not supported."
            )
        else:
            detail = "YouTube app review complete; videos.insert may request public"
        return default_capabilities(direct=self._creds.has_user_context, detail=detail)

    def validate(self, bundle: PostBundle) -> list[str]:
        issues: list[str] = []
        if not (bundle.title or "").strip():
            issues.append("title is empty")
        if not bundle.media_paths:
            issues.append("no video file provided")
        else:
            video = Path(bundle.media_paths[0])
            if video.suffix.lower() not in VIDEO_SUFFIXES:
                issues.append(f"unsupported youtube video type: {video.suffix}")
            if not video.exists():
                issues.append(f"video file missing: {video}")
        visibility = _requested_visibility(bundle)
        if visibility not in ALLOWED_VISIBILITY:
            issues.append(f"invalid visibility {visibility!r}")
        elif visibility == "public" and not self._creds.can_publish_public:
            issues.append("public upload requires completed YouTube API app review")
        if not self._creds.has_user_context:
            issues.append(
                "missing user-context OAuth (user_id + youtube.upload); direct is unavailable"
            )
        return issues

    def publish(
        self,
        bundle: PostBundle,
        account: AccountConfig,
        dry_run: bool = False,
    ) -> PublishResult:
        visibility = _requested_visibility(bundle)
        if dry_run:
            return PublishResult(
                platform_post_id=f"dry-youtube-{visibility}",
                url=None,
                raw_response=json.dumps({
                    "dry_run": True,
                    "platform": "youtube",
                    "account": account.id,
                    "visibility": visibility,
                    "has_user_context": self._creds.has_user_context,
                    "app_reviewed": self._creds.app_reviewed,
                }, ensure_ascii=False),
            )
        if not self._creds.has_user_context:
            raise PublishError(
                "YouTube upload is disabled: missing verifiable user-context OAuth "
                "(user_id + youtube.upload). Public direct publish is not claimed."
            )
        if visibility == "public" and not self._creds.can_publish_public:
            raise PublishError(
                "YouTube public upload is not supported until API app review completes. "
                "Only private/unlisted are allowed."
            )
        if visibility not in ALLOWED_VISIBILITY:
            raise PublishError(f"invalid YouTube visibility {visibility!r}")
        if not bundle.media_paths:
            raise PublishError("youtube upload requires a video file")
        video = Path(bundle.media_paths[0])
        metadata = {
            "snippet": {
                "title": bundle.title,
                "description": (bundle.extra or {}).get("description") or "",
                "tags": list(bundle.tags or ()),
            },
            "status": {"privacyStatus": visibility},
        }
        resp = self._upload(
            self._upload_url,
            headers={"Authorization": f"Bearer {self._creds.access_token}"},
            params={"part": "snippet,status", "uploadType": "multipart"},
            metadata=metadata,
            file_path=video,
            timeout=180.0,
        )
        video_id = resp.get("id")
        if not isinstance(video_id, str) or not video_id.strip():
            raise PublishError("youtube videos.insert returned no id; unknown receipt is failure")
        url = watch_url(video_id)
        return PublishResult(
            platform_post_id=video_id,
            url=url,
            raw_response=json.dumps({
                "platform": "youtube",
                "account": account.id,
                "video_id": video_id,
                "url": url,
                "visibility": visibility,
            }, ensure_ascii=False),
        )

    def compensate(self, platform_post_id: str) -> dict:
        if not platform_post_id:
            raise PublishError("cannot compensate youtube video without id")
        if not self._creds.has_user_context:
            raise PublishError("youtube compensate requires user-context OAuth")
        self._delete(
            self._videos_url,
            headers={"Authorization": f"Bearer {self._creds.access_token}"},
            params={"id": platform_post_id},
            timeout=30.0,
        )
        return {"deleted": platform_post_id}


def _requested_visibility(bundle: PostBundle) -> str:
    extra = bundle.extra or {}
    raw = extra.get("visibility") or extra.get("privacyStatus") or "private"
    return str(raw).strip().lower()


__all__ = [
    "ALLOWED_VISIBILITY",
    "REQUIRED_DIRECT_SCOPES",
    "SAFE_VISIBILITY",
    "SCOPE_UPLOAD",
    "YoutubeCredentials",
    "YoutubePublisher",
    "load_youtube_credential_set",
    "watch_url",
]
