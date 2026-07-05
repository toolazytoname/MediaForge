"""M4-2 X / Twitter Publisher（TECH_SPEC §5.2 + HARD_PARTS §1）。

通过 X API v2 链式发帖。**M4-2 是 M4 阶段最简单平台（OAuth2 + 官方 REST）**，
先跑通框架，M4-3 头条/小红书接入同一 `safe_publish` 入口。

设计要点：
- bearer token 由 caller 注入（secrets/x_<account>.json，含 `{"bearer_token": "..."}`）
  load_x_credentials() 读取；adapter 构造时 resolve 一次
- http_post 注入便于测试（生产 = httpx.post；测试 = lambda）
- thread 拆分：split_thread("1/N ..." / "i/N ..."编号)→ list[tweet]
  容错：忽略非编号行（如标题/简介）
- 链式发布：第 i 条 in_reply_to_tweet_id = 第 i-1 条返回的 id
- 中途失败：抛 PublishError，**错误信息含已发 post id 列表**
  → 编排层（safe_publish）写 publication.error 供人审（人工去平台删除重复帖）
- dry_run：完全跳过 httpx 调用，返回 "dry-" 前缀结果
- 频控：单账号每日 ≤ 3 帖由编排层（config.publish.max_daily_per_account）把关
- LoginExpired：401/403 → 抛 LoginExpired 让编排层停止该平台所有任务

§1 三层防御：
- 配置层：publish.enabled / allowed_platforms / scheduled_at — safe_publish 守
- 乐观锁：UPDATE WHERE status='queued' rowcount==1 — safe_publish 守
- UNIQUE：UNIQUE(content_id, platform, account_id) — DB 守
- INTENT 日志：safe_publish 在 adapter.publish() **前**落日志
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

from pipeline.publishers.base import (
    AccountConfig,
    PostBundle,
    PublishError,
    PublishResult,
    PublisherAdapter,
)


# ── 常量 ───────────────────────────────────────────────────

X_API_BASE = "https://api.twitter.com/2/tweets"
TWEET_MAX_LEN = 260          # M2-3 已 hard-limit（≤ 260 字符，超 X 280 上限留 20 字符余量）
THREAD_MIN_TWEETS = 3
THREAD_MAX_TWEETS = 10


# ── helpers ────────────────────────────────────────────────


def load_x_credentials(path: str | Path) -> str:
    """secrets/x_<account>.json → bearer_token 字符串。

    文件格式：`{"bearer_token": "AAAA..."}`
    缺字段抛 ValueError；文件不存在抛 FileNotFoundError。
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"X credentials file not found: {p}")
    raw = json.loads(p.read_text(encoding="utf-8"))
    token = raw.get("bearer_token")
    if not isinstance(token, str) or not token:
        raise ValueError(
            f"X credentials at {p} missing 'bearer_token' field "
            f"(got keys: {list(raw.keys())})"
        )
    return token


def split_thread(thread_md: str) -> list[str]:
    r"""thread.md 文本 → list[tweet]。

    规则：
    - 按空行分隔段落
    - 每段匹配 ``^(\d+)/(\d+)\s*(.*)$`` 才视为有效推文（\d = 数字）
    - 去掉前缀编号与尾部空白；保留正文（含 emoji / URL / 中文）
    - 非编号段忽略（标题、附录等）
    """
    tweets: list[str] = []
    numbered_re = re.compile(r"^(\d+)\s*/\s*(\d+)\s+(.*)$")
    for block in re.split(r"\n\s*\n", thread_md):
        block = block.strip()
        if not block:
            continue
        m = numbered_re.match(block)
        if not m:
            # 非编号段（标题/附录/空内容）忽略
            continue
        text = m.group(3).strip()
        if text:
            tweets.append(text)
    return tweets


# ── HTTP 客户端（可注入） ──────────────────────────────────


def _httpx_post(
    url: str,
    *,
    headers: dict,
    body: dict,
    timeout: float = 30.0,
) -> dict:
    """默认实现：httpx POST → 解析 JSON 返回。

    仅在 import httpx 时按需调用；测试注入 fake_post 即可跳过。
    401/403 → LoginExpired（让编排层停止该平台其他任务）。
    其他 4xx/5xx → PublishError（含状态码 + body 摘要）。
    """
    import httpx  # 局部 import：避免模块加载时强依赖
    resp = httpx.post(url, headers=headers, json=body, timeout=timeout)
    sc = resp.status_code
    if sc in (401, 403):
        raise PublishError(
            f"X API auth failed ({sc}): login expired?"
        )
    if sc >= 400:
        snippet = resp.text[:300]
        raise PublishError(f"X API error {sc}: {snippet}")
    return resp.json()


# ── XApiPublisher ──────────────────────────────────────────


class XApiPublisher(PublisherAdapter):
    """X / Twitter 平台 PublisherAdapter。

    构造参数：
      - bearer_token: 字符串
      - http_post:    可调用 (url, *, headers, body, timeout) -> dict
                      生产传 _httpx_post（默认）；测试传 fake 实现

    platform = "x"。
    """

    platform = "x"

    def __init__(
        self,
        *,
        bearer_token: str,
        http_post: Callable[..., dict] | None = None,
        api_base: str = X_API_BASE,
    ) -> None:
        if not bearer_token:
            raise ValueError("XApiPublisher requires bearer_token")
        self._token = bearer_token
        self._post = http_post or _httpx_post
        self._api = api_base.rstrip("/")

    # ── 本地校验（不触网络） ──

    def validate(self, bundle: PostBundle) -> list[str]:
        """本地校验（不触网络）。"""
        issues: list[str] = []
        # bundle.body_path 来自 safe_publish.build_post_bundle 的 canonical_path
        # 派生文件按 ARCHITECTURE §8 在 <content_dir>/x/thread.md
        # 同时允许 caller 直接传 x/thread.md（单测场景）
        thread_path = bundle.body_path
        if not thread_path.exists():
            thread_path = bundle.body_path.parent / "x" / "thread.md"
        if not thread_path.exists():
            issues.append(
                f"thread.md not found at {bundle.body_path} nor at {thread_path}"
            )
            return issues
        try:
            body = thread_path.read_text(encoding="utf-8")
        except OSError as e:
            issues.append(f"cannot read thread.md: {e}")
            return issues
        tweets = split_thread(body)
        if not tweets:
            issues.append("thread is empty (no numbered tweets found)")
        if len(tweets) < THREAD_MIN_TWEETS:
            issues.append(
                f"thread too short: {len(tweets)} tweets "
                f"(need {THREAD_MIN_TWEETS}-{THREAD_MAX_TWEETS})"
            )
        if len(tweets) > THREAD_MAX_TWEETS:
            issues.append(
                f"thread too long: {len(tweets)} tweets "
                f"(max {THREAD_MAX_TWEETS}, min {THREAD_MIN_TWEETS})"
            )
        over = [i for i, t in enumerate(tweets, 1) if len(t) > TWEET_MAX_LEN]
        if over:
            issues.append(
                f"tweets over {TWEET_MAX_LEN} chars at positions: "
                f"{over} (lengths: {[len(tweets[i-1]) for i in over]})"
            )
        return issues

    # ── 真实发布 ──

    def publish(
        self,
        bundle: PostBundle,
        account: AccountConfig,
        dry_run: bool = False,
    ) -> PublishResult:
        # 决定 thread.md 实际路径（见 validate 注释）
        thread_path = bundle.body_path
        if not thread_path.exists():
            thread_path = bundle.body_path.parent / "x" / "thread.md"
        if not thread_path.exists():
            raise PublishError(
                f"thread.md not found at {bundle.body_path} nor {thread_path}"
            )
        tweets = split_thread(thread_path.read_text(encoding="utf-8"))

        # dry-run：不调 HTTP；返回 dry- 模拟结果
        if dry_run:
            return PublishResult(
                platform_post_id=f"dry-{len(tweets)}tweets",
                url=None,
                raw_response=json.dumps({
                    "dry_run": True,
                    "platform": "x",
                    "account": account.id,
                    "tweet_count": len(tweets),
                }, ensure_ascii=False),
            )
        if not tweets:
            raise PublishError("thread is empty (cannot publish 0 tweets)")

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

        published_ids: list[str] = []
        last_tweet_id: str | None = None
        for i, tweet in enumerate(tweets, 1):
            payload: dict = {"text": tweet}
            if last_tweet_id is not None:
                payload["in_reply_to_tweet_id"] = last_tweet_id
            try:
                resp = self._post(self._api, headers=headers, body=payload, timeout=30.0)
            except PublishError as e:
                # 业务异常：重新包一层，把已发 id 一并塞进 message（人工删重帖用）
                partial = ",".join(published_ids) if published_ids else "(none)"
                raise PublishError(
                    f"X API failed on tweet {i}/{len(tweets)}: {e}; "
                    f"partial_published_ids={partial}; manual cleanup needed"
                ) from e
            except Exception as e:
                partial = ",".join(published_ids) if published_ids else "(none)"
                raise PublishError(
                    f"X API unexpected error on tweet {i}/{len(tweets)} "
                    f"(published so far: {partial}): {e!r}"
                ) from e

            data = resp.get("data") if isinstance(resp, dict) else None
            post_id = (data or {}).get("id") if isinstance(data, dict) else None
            if not post_id:
                partial = ",".join(published_ids) if published_ids else "(none)"
                raise PublishError(
                    f"X API returned no tweet id on tweet {i}/{len(tweets)}; "
                    f"partial_published_ids={partial}; "
                    f"response={resp!r}"
                )
            published_ids.append(post_id)
            last_tweet_id = post_id

        # 全部成功
        first_id = published_ids[0]
        return PublishResult(
            platform_post_id=first_id,
            url=f"https://x.com/i/status/{first_id}",
            raw_response=json.dumps({
                "platform": "x",
                "account": account.id,
                "thread_ids": published_ids,
                "tweet_count": len(tweets),
            }, ensure_ascii=False),
        )


__all__ = [
    "XApiPublisher",
    "load_x_credentials",
    "split_thread",
    "X_API_BASE",
    "TWEET_MAX_LEN",
    "THREAD_MIN_TWEETS",
    "THREAD_MAX_TWEETS",
]
