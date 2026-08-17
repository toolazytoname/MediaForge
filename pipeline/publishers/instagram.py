"""Instagram Professional container + media_publish.

Requires a Professional (Business/Creator) user token, a publicly fetchable
HTTPS media URL, and app review. Missing any of those fail-closes. Success
requires a published media id; unknown receipts fail.
"""
from __future__ import annotations

import ipaddress
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

GRAPH_BASE = "https://graph.facebook.com/v21.0"
INSTAGRAM_AUTHORIZE_URL = "https://www.instagram.com/oauth/authorize"
SCOPE_PUBLISH = "instagram_content_publish"
SCOPE_PUBLISH_BUSINESS = "instagram_business_content_publish"
SCOPE_BASIC = "instagram_basic"
REQUIRED_DIRECT_SCOPES = frozenset({SCOPE_PUBLISH, SCOPE_PUBLISH_BUSINESS})
VIDEO_SUFFIXES = {".mp4", ".mov", ".webm"}


@dataclass(frozen=True)
class InstagramCredentials:
    access_token: str
    user_id: str | None = None
    scopes: tuple[str, ...] = ()
    app_reviewed: bool = False

    @property
    def has_user_context(self) -> bool:
        if not self.access_token or not self.user_id:
            return False
        have = {scope.lower() for scope in self.scopes}
        return bool(REQUIRED_DIRECT_SCOPES & have)

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


def load_instagram_credential_set(path: str | Path) -> InstagramCredentials:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"instagram credentials file not found: {p}")
    raw = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"instagram credentials at {p} must be a JSON object")
    token = raw.get("access_token") or raw.get("user_access_token")
    if not isinstance(token, str) or not token:
        return InstagramCredentials(access_token="")
    user_id = raw.get("user_id") or raw.get("ig_user_id")
    if user_id is not None:
        user_id = str(user_id).strip() or None
    return InstagramCredentials(
        access_token=token,
        user_id=user_id,
        scopes=_parse_scopes(raw.get("scopes") or raw.get("scope")),
        app_reviewed=bool(raw.get("app_reviewed") or raw.get("has_app_review")),
    )


def is_public_https_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc or not parsed.path:
        return False
    host = (parsed.hostname or "").lower()
    if not host or host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"} or host.endswith(".local"):
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return True
    return addr.is_global


def _media_urls(bundle: PostBundle) -> list[str]:
    extra = bundle.extra or {}
    raw = extra.get("media_urls") or extra.get("image_urls")
    urls: list[str] = []
    if isinstance(raw, (list, tuple)):
        urls.extend(str(item).strip() for item in raw if str(item).strip())
    single = extra.get("media_url") or extra.get("image_url") or extra.get("video_url")
    if isinstance(single, str) and single.strip():
        urls.append(single.strip())
    return list(dict.fromkeys(urls))


def _httpx_json(
    url: str,
    *,
    headers: dict,
    params: dict | None = None,
    body: dict | None = None,
    method: str = "POST",
    timeout: float = 30.0,
) -> dict:
    import httpx
    try:
        verb = method.upper()
        if verb == "DELETE":
            resp = httpx.delete(url, headers=headers, params=params, timeout=timeout)
        else:
            resp = httpx.post(url, headers=headers, params=params, json=body, timeout=timeout)
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        raise PublishError(f"instagram API network error: {exc!r}") from exc
    return _parse_http_response(resp, url)


def _parse_http_response(resp: object, url: str) -> dict:
    status = getattr(resp, "status_code", 0)
    text = getattr(resp, "text", "") or ""
    if status in (401, 403):
        raise LoginExpired(f"instagram API auth failed ({status}) at {url}")
    if status >= 400:
        raise PublishError(f"instagram API HTTP {status} at {url}: {text[:300]}")
    if not text:
        return {}
    try:
        data = resp.json()  # type: ignore[union-attr]
    except ValueError as exc:
        raise PublishError(f"instagram API non-JSON at {url}: {text[:300]!r}") from exc
    if not isinstance(data, dict):
        raise PublishError(f"instagram API bad response shape at {url}")
    error = data.get("error")
    if isinstance(error, dict):
        raise PublishError(f"instagram API error: {error.get('message') or error}")
    return data


def permalink(media_id: str) -> str:
    return f"https://www.instagram.com/p/{media_id}/"


class InstagramPublisher(PublisherAdapter):
    platform = "instagram"

    def __init__(
        self,
        *,
        credentials: InstagramCredentials | None = None,
        access_token: str = "",
        user_id: str | None = None,
        scopes: Iterable[str] = (),
        app_reviewed: bool = False,
        http_post: Callable[..., dict] | None = None,
        api_base: str = GRAPH_BASE,
    ) -> None:
        if credentials is not None:
            self._creds = credentials
        else:
            self._creds = InstagramCredentials(
                access_token=access_token,
                user_id=user_id,
                scopes=tuple(scopes),
                app_reviewed=app_reviewed,
            )
        self._post = http_post or _httpx_json
        self._api = api_base.rstrip("/")

    def capabilities(self) -> AdapterCapabilities:
        if not self._creds.has_user_context:
            detail = (
                "Instagram media_publish fail-closed: need Professional user OAuth "
                f"(ig_user_id + {SCOPE_PUBLISH}). Public direct is not claimed."
            )
        elif not self._creds.can_publish_public:
            detail = (
                "Instagram user-context present but app review is incomplete. "
                "container + media_publish will not run. Public direct is not supported."
            )
        else:
            detail = (
                "Instagram Professional OAuth + app review present; "
                "container + media_publish requires a public HTTPS media URL"
            )
        return default_capabilities(direct=self._creds.can_publish_public, detail=detail)

    def validate(self, bundle: PostBundle) -> list[str]:
        issues: list[str] = []
        if not (bundle.title or "").strip() and not (bundle.extra or {}).get("caption"):
            issues.append("caption is empty")
        urls = _media_urls(bundle)
        if not urls:
            issues.append(
                "no publicly fetchable HTTPS media URL; local files cannot be published"
            )
        for url in urls:
            if not is_public_https_url(url):
                issues.append(f"media URL is not a public HTTPS URL: {url}")
        if not self._creds.has_user_context:
            issues.append(
                "missing Professional user-context OAuth (ig_user_id + "
                f"{SCOPE_PUBLISH}); direct is unavailable"
            )
        elif not self._creds.can_publish_public:
            issues.append("Instagram publish requires completed app review; public direct is not claimed")
        return issues

    def publish(
        self,
        bundle: PostBundle,
        account: AccountConfig,
        dry_run: bool = False,
    ) -> PublishResult:
        urls = _media_urls(bundle)
        if dry_run:
            return PublishResult(
                platform_post_id="dry-instagram",
                url=None,
                raw_response=json.dumps({
                    "dry_run": True,
                    "platform": "instagram",
                    "account": account.id,
                    "has_user_context": self._creds.has_user_context,
                    "app_reviewed": self._creds.app_reviewed,
                    "media_url_count": len(urls),
                    "public_direct_claimed": False,
                }, ensure_ascii=False),
            )
        if not self._creds.has_user_context:
            raise PublishError(
                "Instagram publish is disabled: missing Professional user-context OAuth "
                f"(ig_user_id + {SCOPE_PUBLISH}). Public direct is not claimed."
            )
        if not self._creds.can_publish_public:
            raise PublishError(
                "Instagram media_publish is not supported until app review completes. "
                "Public direct is not claimed."
            )
        if not urls:
            raise PublishError(
                "Instagram media_publish requires a publicly fetchable HTTPS media URL"
            )
        bad = [url for url in urls if not is_public_https_url(url)]
        if bad:
            raise PublishError(f"Instagram media URL is not publicly fetchable HTTPS: {bad[0]}")
        caption = str((bundle.extra or {}).get("caption") or bundle.title or "")
        if len(urls) == 1:
            creation_id = self._create_container(urls[0], caption, bundle)
        else:
            children = [self._create_container(url, "", bundle, is_carousel_item=True) for url in urls]
            creation_id = self._create_carousel(children, caption)
        published = self._post(
            f"{self._api}/{self._creds.user_id}/media_publish",
            headers=self._headers(),
            body={"creation_id": creation_id},
            timeout=30.0,
        )
        media_id = published.get("id")
        if not isinstance(media_id, str) or not media_id.strip():
            raise PublishError("instagram media_publish returned no id; unknown receipt is failure")
        url = permalink(media_id)
        return PublishResult(
            platform_post_id=media_id,
            url=url,
            raw_response=json.dumps({
                "platform": "instagram",
                "account": account.id,
                "media_id": media_id,
                "creation_id": creation_id,
                "url": url,
            }, ensure_ascii=False),
        )

    def compensate(self, platform_post_id: str) -> dict:
        if not platform_post_id:
            raise PublishError("cannot compensate instagram media without id")
        if not self._creds.has_user_context:
            raise PublishError("instagram compensate requires user-context OAuth")
        self._post(
            f"{self._api}/{platform_post_id}",
            headers=self._headers(),
            method="DELETE",
            timeout=30.0,
        )
        return {"deleted": platform_post_id}

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._creds.access_token}",
            "Content-Type": "application/json",
        }

    def _create_container(
        self,
        media_url: str,
        caption: str,
        bundle: PostBundle,
        *,
        is_carousel_item: bool = False,
    ) -> str:
        extra = bundle.extra or {}
        media_type = str(extra.get("media_type") or "").upper()
        suffix = Path(urlparse(media_url).path).suffix.lower()
        body: dict[str, object] = {}
        if media_type in {"REELS", "VIDEO"} or suffix in VIDEO_SUFFIXES:
            body["media_type"] = "REELS" if media_type != "VIDEO" else "VIDEO"
            body["video_url"] = media_url
        else:
            body["image_url"] = media_url
        if caption and not is_carousel_item:
            body["caption"] = caption
        if is_carousel_item:
            body["is_carousel_item"] = True
        created = self._post(
            f"{self._api}/{self._creds.user_id}/media",
            headers=self._headers(),
            body=body,
            timeout=30.0,
        )
        creation_id = created.get("id")
        if not isinstance(creation_id, str) or not creation_id.strip():
            raise PublishError("instagram container create returned no id; unknown receipt is failure")
        return creation_id.strip()

    def _create_carousel(self, children: list[str], caption: str) -> str:
        body: dict[str, object] = {
            "media_type": "CAROUSEL",
            "children": ",".join(children),
        }
        if caption:
            body["caption"] = caption
        created = self._post(
            f"{self._api}/{self._creds.user_id}/media",
            headers=self._headers(),
            body=body,
            timeout=30.0,
        )
        creation_id = created.get("id")
        if not isinstance(creation_id, str) or not creation_id.strip():
            raise PublishError("instagram carousel create returned no id; unknown receipt is failure")
        return creation_id.strip()


__all__ = [
    "GRAPH_BASE",
    "INSTAGRAM_AUTHORIZE_URL",
    "InstagramCredentials",
    "InstagramPublisher",
    "REQUIRED_DIRECT_SCOPES",
    "SCOPE_BASIC",
    "SCOPE_PUBLISH",
    "is_public_https_url",
    "load_instagram_credential_set",
    "permalink",
]
