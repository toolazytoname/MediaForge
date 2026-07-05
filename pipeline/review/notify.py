"""M2-5 — webhook 通知（飞书/TG bot 兼容格式）。

仅在 config.notify.webhook_url 非空时触发；失败不阻断主流程
（HARD_PARTS §2 + §9：IM 挂掉不应阻断审稿）。
"""
from __future__ import annotations

from pathlib import Path

import httpx

from pipeline.utils.log import get_logger, log_event


_TIMEOUT_S = 5.0


def notify_review(
    *,
    webhook_url: str | None,
    count: int,
    review_path: Path | str,
) -> bool:
    """推送"今日 N 篇待审 + 路径"。

    返回 True 表示已成功推送（200/2xx）；False 表示未推送或失败。
    webhook_url 为 None → 直接返回 False，不打网络。
    """
    logger = get_logger("review.notify")
    if not webhook_url:
        log_event(
            logger, 20,
            "review.notify: webhook_url not set, skip",
            stage="review",
        )
        return False

    text = (
        f"📝 MediaForge 审核提醒：今日 {count} 篇待审\n"
        f"清单: {review_path}"
    )
    # 飞书/TG bot 通用 JSON 出口
    body = {"text": text}

    try:
        resp = httpx.post(
            webhook_url, json=body, timeout=_TIMEOUT_S,
        )
        resp.raise_for_status()
        log_event(
            logger, 20,
            f"review.notify: posted ok status={resp.status_code}",
            stage="review",
        )
        return True
    except Exception as e:
        # 任何异常：仅 warn，绝不抛（HARD_PARTS §9 IM 失败不阻塞主流程）
        log_event(
            logger, 30,
            f"review.notify: failed: {e}",
            stage="review",
        )
        return False