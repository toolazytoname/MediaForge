"""公众号（wechat_mp）Publisher —— 官方草稿箱 API（M13）。

移植来源：
  - TrendPublish `src/integrations/publish/providers/weixin-api-client.ts`
    （164 行，access_token 获取与缓存 + classifyWeixinError 错误分类）
  - TrendPublish `src/integrations/publish/providers/weixin-publisher.ts`
    （355 行，uploadImage/uploadDraft 两步流程）
  - TrendPublish 的 uploadContentImage（正文内嵌图片上传，`media/uploadimg` 接口）
    现已补齐：真实发布时把正文里本地相对路径插图（由
    `pipeline.creators.derivative_wechat_mp.insert_generated_images` 拼接产出的
    `../images/inline-N.png` 引用）逐个上传成微信 CDN 地址后再替换进正文，
    因为 draft/add 的 HTML 正文里图片必须是微信 CDN url，不能是本地路径。

前置条件（操作性，非代码缺陷，见 docs/HARD_PARTS.md）：
  - access_token 请求要求出口 IP 在公众号后台加白名单，否则 errcode 40164
  - 个人/未认证订阅号大概率没有 draft/add 权限，errcode 40001/48001

草稿箱是终态安全动作：draft/add 只创建草稿，不会真正群发，仍需人工去
公众号后台手动点发布——这与 X/头条/小红书"发出去就是发出去了"不同，
是本平台唯一天然自带的一层人审兜底。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Callable

from pipeline.creators.wechat_html import (
    WechatImageMeta,
    decorate_wechat_content_images,
    markdown_to_wechat_html,
)
from pipeline.publishers.base import (
    AccountConfig,
    LoginExpired,
    PostBundle,
    PublishError,
    PublishResult,
    PublisherAdapter,
)

WECHAT_API_BASE = "https://api.weixin.qq.com/cgi-bin"
TITLE_MAX_LEN = 64        # 微信草稿标题上限
DIGEST_MAX_LEN = 120       # draft/add digest 字段上限
COVER_MAX_BYTES = 10 * 1024 * 1024   # material/add_material 上限

_TOKEN_TTL_S = 7200                 # access_token 官方有效期
_TOKEN_REFRESH_MARGIN_S = 60        # 提前刷新余量

_AUTH_ERRCODES = {40001, 40013, 40125, 40164, 48001}
_RATE_LIMIT_ERRCODES = {45009, 45011}
_QUOTA_ERRCODES = {45008, 45028}

# 正文图片的本地相对路径：封面 + 插图。封面默认只进 thumb_media_id，
# 公众号正文要再走一遍 uploadimg，否则编辑器里只有文字。
_INLINE_IMAGE_MD_RE = re.compile(
    r"!\[([^\]]*)\]\((\.\./(?:cover|images/inline-\d+)\.png)\)"
)
_COVER_BODY_MARKDOWN = "![封面](../cover.png)"


# ── 凭据 ───────────────────────────────────────────────────

def load_wechat_credentials(path: str | Path) -> tuple[str, str]:
    """secrets/wechat_mp_<account>.json → (app_id, app_secret)。

    文件格式：`{"app_id": "...", "app_secret": "..."}`
    缺文件/缺字段抛 FileNotFoundError/ValueError（同 load_x_credentials 风格）。
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"wechat_mp credentials file not found: {p}")
    raw = json.loads(p.read_text(encoding="utf-8"))
    app_id = raw.get("app_id")
    app_secret = raw.get("app_secret")
    if not isinstance(app_id, str) or not app_id:
        raise ValueError(
            f"wechat_mp credentials at {p} missing 'app_id' field "
            f"(got keys: {list(raw.keys())})"
        )
    if not isinstance(app_secret, str) or not app_secret:
        raise ValueError(
            f"wechat_mp credentials at {p} missing 'app_secret' field "
            f"(got keys: {list(raw.keys())})"
        )
    return app_id, app_secret


# ── 错误分类 ───────────────────────────────────────────────

def _classify_wechat_error(errcode: int, errmsg: str) -> PublishError:
    """微信 errcode → PublishError / LoginExpired。"""
    if errcode in _AUTH_ERRCODES:
        hint = ""
        if errcode == 40164:
            hint = "（出口 IP 需加入公众号后台 IP 白名单）"
        elif errcode == 48001:
            hint = "（个人/未认证账号大概率无此接口权限，需公众号完成认证）"
        return LoginExpired(
            f"wechat_mp auth/permission error {errcode}: {errmsg}{hint}"
        )
    if errcode in _RATE_LIMIT_ERRCODES:
        return PublishError(f"wechat_mp rate_limit error {errcode}: {errmsg}")
    if errcode in _QUOTA_ERRCODES:
        return PublishError(f"wechat_mp quota error {errcode}: {errmsg}")
    return PublishError(f"wechat_mp API error {errcode}: {errmsg}")


# ── HTTP 客户端（可注入） ──────────────────────────────────

def _parse_json_response(resp, url: str) -> dict:
    if resp.status_code >= 400:
        raise PublishError(
            f"wechat_mp HTTP error {resp.status_code} at {url}: {resp.text[:300]}"
        )
    try:
        data = resp.json()
    except ValueError as e:
        raise PublishError(
            f"wechat_mp returned non-JSON at {url}: {resp.text[:300]!r}"
        ) from e
    if isinstance(data, dict) and data.get("errcode", 0) != 0:
        raise _classify_wechat_error(data["errcode"], data.get("errmsg", ""))
    return data


def _httpx_get(url: str, *, params: dict, timeout: float = 30.0) -> dict:
    import httpx  # 局部 import：避免模块加载时强依赖
    try:
        resp = httpx.get(url, params=params, timeout=timeout)
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        raise PublishError(f"wechat_mp network error (GET {url}): {e!r}") from e
    return _parse_json_response(resp, url)


def _httpx_post(url: str, *, params: dict, json_body: dict, timeout: float = 30.0) -> dict:
    import httpx
    try:
        resp = httpx.post(url, params=params, json=json_body, timeout=timeout)
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        raise PublishError(f"wechat_mp network error (POST {url}): {e!r}") from e
    return _parse_json_response(resp, url)


def _httpx_upload(url: str, *, params: dict, file_path: Path, timeout: float = 60.0) -> dict:
    import httpx
    try:
        with file_path.open("rb") as f:
            files = {"media": (file_path.name, f, "application/octet-stream")}
            resp = httpx.post(url, params=params, files=files, timeout=timeout)
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        raise PublishError(f"wechat_mp network error (UPLOAD {url}): {e!r}") from e
    return _parse_json_response(resp, url)


# ── WechatMpPublisher ──────────────────────────────────────

class WechatMpPublisher(PublisherAdapter):
    """公众号（微信官方草稿箱 API）PublisherAdapter。

    构造参数：
      - app_id/app_secret: 字符串
      - http_get/http_post/http_upload: 可注入（生产=_httpx_*；测试=fake）
        三个注入点分别对应 token（GET）/ draft（JSON POST）/ 封面（multipart 上传）。

    platform = "wechat_mp"。
    """

    platform = "wechat_mp"

    def capabilities(self):
        from pipeline.publishers.capabilities import default_capabilities
        return default_capabilities(
            draft=True,
            direct=True,
            detail="WeChat MP draft/add plus gated freepublish/submit; unknown receipts must not auto-retry",
        )

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        http_get: Callable[..., dict] | None = None,
        http_post: Callable[..., dict] | None = None,
        http_upload: Callable[..., dict] | None = None,
        api_base: str = WECHAT_API_BASE,
    ) -> None:
        if not app_id or not app_secret:
            raise ValueError("WechatMpPublisher requires app_id and app_secret")
        self._app_id = app_id
        self._app_secret = app_secret
        self._get = http_get or _httpx_get
        self._post = http_post or _httpx_post
        self._upload = http_upload or _httpx_upload
        self._api = api_base.rstrip("/")
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    def _ensure_access_token(self) -> str:
        """内存缓存 access_token；到期（含 60s 安全余量）前直接复用。

        dry_run 发布路径完全不会调用本方法，见 publish()。
        """
        now = time.monotonic()
        if self._token is not None and now < self._token_expires_at:
            return self._token
        data = self._get(
            f"{self._api}/token",
            params={
                "grant_type": "client_credential",
                "appid": self._app_id,
                "secret": self._app_secret,
            },
        )
        token = data.get("access_token")
        if not isinstance(token, str) or not token:
            raise PublishError(f"wechat_mp token response missing access_token: {data!r}")
        expires_in = data.get("expires_in", _TOKEN_TTL_S)
        self._token = token
        self._token_expires_at = now + max(0, expires_in - _TOKEN_REFRESH_MARGIN_S)
        return token

    # ── 本地校验（不触网络） ──

    def validate(self, bundle: PostBundle) -> list[str]:
        """本地校验：wechat_mp/meta.json + article.md 存在、title/digest 长度、
        封面文件存在且体积达标。不发任何网络请求。"""
        issues: list[str] = []
        content_dir = bundle.body_path.parent
        wechat_dir = content_dir / "wechat_mp"
        meta_path = wechat_dir / "meta.json"
        article_path = wechat_dir / "article.md"

        if not meta_path.exists():
            issues.append(f"meta.json not found at {meta_path}")
        else:
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                issues.append(f"cannot read/parse meta.json: {e}")
                meta = {}
            title = meta.get("title", "") if isinstance(meta, dict) else ""
            digest = meta.get("digest", "") if isinstance(meta, dict) else ""
            if not title:
                issues.append("meta.json missing 'title'")
            elif len(title) > TITLE_MAX_LEN:
                issues.append(
                    f"title too long: {len(title)} chars (max {TITLE_MAX_LEN})"
                )
            if not digest:
                issues.append("meta.json missing 'digest'")
            elif len(digest) > DIGEST_MAX_LEN:
                issues.append(
                    f"digest too long: {len(digest)} chars (max {DIGEST_MAX_LEN})"
                )

        if not article_path.exists():
            issues.append(f"article.md not found at {article_path}")
        else:
            try:
                body = article_path.read_text(encoding="utf-8")
            except OSError as e:
                issues.append(f"cannot read article.md: {e}")
                body = ""
            if not body.strip():
                issues.append("article.md is empty")
            for _caption, rel_path in _INLINE_IMAGE_MD_RE.findall(body):
                inline_path = (wechat_dir / rel_path).resolve()
                if not inline_path.exists():
                    issues.append(f"inline image not found at {inline_path}")

        cover_path = content_dir / "cover.png"
        if not cover_path.exists():
            issues.append(f"cover image not found at {cover_path}")
        else:
            try:
                size = cover_path.stat().st_size
            except OSError as e:
                issues.append(f"cannot stat cover image: {e}")
            else:
                if size == 0:
                    issues.append(f"cover image is empty: {cover_path}")
                if size > COVER_MAX_BYTES:
                    issues.append(
                        f"cover image too large: {size} bytes (max {COVER_MAX_BYTES})"
                    )
        return issues

    # ── 真实发布 ──

    def publish(
        self,
        bundle: PostBundle,
        account: AccountConfig,
        dry_run: bool = False,
    ) -> PublishResult:
        content_dir = bundle.body_path.parent
        wechat_dir = content_dir / "wechat_mp"
        meta_path = wechat_dir / "meta.json"
        article_path = wechat_dir / "article.md"
        cover_path = content_dir / "cover.png"

        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise PublishError(f"cannot read meta.json at {meta_path}: {e}") from e
        title = meta.get("title", "")
        digest = meta.get("digest", "")

        try:
            article_md = article_path.read_text(encoding="utf-8")
        except OSError as e:
            raise PublishError(f"cannot read article.md at {article_path}: {e}") from e

        extra = dict(bundle.extra or {})
        delivery_mode = extra.get("delivery_mode")
        existing_draft = extra.get("draft_media_id")
        want_direct = delivery_mode == "direct"

        # dry-run：不调 HTTP；返回 dry- 模拟结果
        if dry_run:
            prefix = "dry-direct-" if want_direct else "dry-draft-"
            return PublishResult(
                platform_post_id=f"{prefix}{bundle.content_id}",
                url=None,
                raw_response=json.dumps({
                    "dry_run": True,
                    "platform": "wechat_mp",
                    "account": account.id,
                    "title": title,
                    "digest": digest,
                    "cover": str(cover_path),
                    "delivery_mode": delivery_mode or "draft",
                }, ensure_ascii=False),
            )

        if isinstance(existing_draft, str) and existing_draft.strip():
            media_id = existing_draft.strip()
            thumb_media_id = None
        else:
            if not cover_path.exists():
                raise PublishError(f"cover image not found at {cover_path}")

            article_md = self._ensure_cover_in_body(article_md)
            article_md, image_metas = self._inline_images_to_cdn(article_md, wechat_dir)
            html_body = decorate_wechat_content_images(
                markdown_to_wechat_html(article_md), image_metas,
            )
            thumb_media_id = self._upload_cover(cover_path)
            draft_resp = self._upload_draft(
                title=title, digest=digest, html_body=html_body,
                thumb_media_id=thumb_media_id,
            )
            media_id = draft_resp.get("media_id")
            if not isinstance(media_id, str) or not media_id:
                raise PublishError(f"wechat_mp draft/add missing media_id: {draft_resp!r}")

        if not want_direct:
            # 草稿箱没有公开 url（需人工在公众号后台手动发布），url=None 是正确行为
            return PublishResult(
                platform_post_id=media_id,
                url=None,
                raw_response=json.dumps({
                    "platform": "wechat_mp",
                    "account": account.id,
                    "draft_media_id": media_id,
                    "thumb_media_id": thumb_media_id,
                }, ensure_ascii=False),
            )

        return self._freepublish(media_id=media_id, account_id=account.id)

    def _upload_cover(self, cover_path: Path) -> str:
        token = self._ensure_access_token()
        data = self._upload(
            f"{self._api}/material/add_material",
            params={"access_token": token, "type": "image"},
            file_path=cover_path,
        )
        media_id = data.get("media_id")
        if not isinstance(media_id, str) or not media_id:
            raise PublishError(f"wechat_mp cover upload missing media_id: {data!r}")
        return media_id

    def _upload_content_image(self, image_path: Path) -> str:
        token = self._ensure_access_token()
        data = self._upload(
            f"{self._api}/media/uploadimg",
            params={"access_token": token},
            file_path=image_path,
        )
        url = data.get("url")
        if not isinstance(url, str) or not url:
            raise PublishError(f"wechat_mp content image upload missing url: {data!r}")
        return url

    def _ensure_cover_in_body(self, article_md: str) -> str:
        """封面默认只作为 thumb_media_id；正文也插入一张，编辑器才能看见配图。"""
        if "../cover.png" in article_md:
            return article_md
        return f"{_COVER_BODY_MARKDOWN}\n\n{article_md}"

    def _inline_images_to_cdn(
        self, article_md: str, wechat_dir: Path,
    ) -> tuple[str, dict[str, WechatImageMeta]]:
        """把正文里本地相对路径图片上传到微信 CDN，并记下宽高供编辑器属性使用。"""
        metas: dict[str, WechatImageMeta] = {}

        def _replace(match: re.Match) -> str:
            caption, rel_path = match.group(1), match.group(2)
            local_path = (wechat_dir / rel_path).resolve()
            if not local_path.exists():
                raise PublishError(f"inline image not found for upload: {local_path}")
            cdn_url = self._upload_content_image(local_path)
            metas[cdn_url] = _image_meta(local_path, cdn_url)
            return f"![{caption}]({cdn_url})"

        return _INLINE_IMAGE_MD_RE.sub(_replace, article_md), metas

    def _upload_draft(
        self, *, title: str, digest: str, html_body: str, thumb_media_id: str,
    ) -> dict:
        token = self._ensure_access_token()
        return self._post(
            f"{self._api}/draft/add",
            params={"access_token": token},
            json_body={
                "articles": [{
                    "title": title,
                    "author": "",
                    "digest": digest,
                    "content": html_body,
                    "thumb_media_id": thumb_media_id,
                    "show_cover_pic": 1,
                    "need_open_comment": 0,
                    "only_fans_can_comment": 0,
                }]
            },
        )

    def _freepublish(self, *, media_id: str, account_id: str) -> PublishResult:
        try:
            submit = self._freepublish_submit(media_id)
        except PublishError as error:
            # Official rejection is definitive; transport/HTTP/parse failures are not.
            if any(marker in str(error) for marker in (
                "auth/permission error", "API error", "quota error", "rate_limit error",
            )):
                raise
            raise PublishError(f"unknown receipt: freepublish submit: {error}") from error
        publish_id = submit.get("publish_id") if isinstance(submit, dict) else None
        if not isinstance(publish_id, str) or not publish_id:
            raise PublishError(f"unknown receipt: wechat_mp freepublish/submit missing publish_id: {submit!r}")
        try:
            status = self._freepublish_get(publish_id)
        except Exception as error:
            raise PublishError(f"unknown receipt: query failed publish_id={publish_id}: {error}") from error
        if not isinstance(status, dict):
            raise PublishError(f"unknown receipt: invalid status publish_id={publish_id}")
        publish_status = status.get("publish_status")
        payload = {
            "platform": "wechat_mp",
            "account": account_id,
            "draft_media_id": media_id,
            "publish_id": publish_id,
            "publish_status": publish_status,
            "article_id": status.get("article_id"),
            "article_url": status.get("article_url") or status.get("url"),
        }
        if publish_status == 1:
            raise PublishError(
                "unknown receipt: freepublish still publishing; do not retry "
                f"publish_id={publish_id}"
            )
        if publish_status in (2, 3, 5, 6):
            raise PublishError(f"wechat_mp freepublish failed status={publish_status!r}: {status!r}")
        if publish_status not in (0, 4):
            raise PublishError(f"unknown receipt: status={publish_status!r} publish_id={publish_id}")
        article_id = status.get("article_id")
        if not isinstance(article_id, str) or not article_id:
            raise PublishError(
                f"unknown receipt: wechat_mp freepublish missing article_id publish_id={publish_id}: {status!r}"
            )
        url = payload["article_url"]
        if not isinstance(url, str) or not url:
            url = None
        return PublishResult(
            platform_post_id=article_id,
            url=url,
            raw_response=json.dumps(payload, ensure_ascii=False),
        )

    def _freepublish_submit(self, media_id: str) -> dict:
        token = self._ensure_access_token()
        return self._post(
            f"{self._api}/freepublish/submit",
            params={"access_token": token},
            json_body={"media_id": media_id},
        )

    def query_freepublish(self, publish_id: str) -> dict:
        return self._freepublish_get(publish_id)

    def _freepublish_get(self, publish_id: str) -> dict:
        token = self._ensure_access_token()
        return self._post(
            f"{self._api}/freepublish/get",
            params={"access_token": token},
            json_body={"publish_id": publish_id},
        )

    def _update_draft(
        self, *, media_id: str, title: str, digest: str,
        html_body: str, thumb_media_id: str,
    ) -> dict:
        token = self._ensure_access_token()
        return self._post(
            f"{self._api}/draft/update",
            params={"access_token": token},
            json_body={
                "media_id": media_id,
                "index": 0,
                "articles": {
                    "title": title,
                    "author": "",
                    "digest": digest,
                    "content": html_body,
                    "thumb_media_id": thumb_media_id,
                    "show_cover_pic": 1,
                    "need_open_comment": 0,
                    "only_fans_can_comment": 0,
                },
            },
        )


def _image_meta(path: Path, cdn_url: str) -> WechatImageMeta:
    image_type = path.suffix.lstrip(".").lower() or "png"
    if image_type == "jpeg":
        image_type = "jpg"
    width = height = None
    try:
        from PIL import Image
        with Image.open(path) as image:
            width, height = image.size
            if image.format:
                image_type = image.format.lower()
                if image_type == "jpeg":
                    image_type = "jpg"
    except OSError:
        pass
    return WechatImageMeta(
        url=cdn_url, width=width, height=height, image_type=image_type,
    )


__all__ = [
    "WechatMpPublisher",
    "load_wechat_credentials",
    "WECHAT_API_BASE",
    "TITLE_MAX_LEN",
    "DIGEST_MAX_LEN",
    "COVER_MAX_BYTES",
]
