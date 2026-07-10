"""M10-4 + M10 P2 阶段 A/B/D contents router。

GET  /api/v1/contents                内容列表（status/pillar 过滤 + 分页）
GET  /api/v1/contents/{id}           内容详情：canonical.md → HTML + 派生文件 +
                                     图卡 URL + 关联 publications
POST /api/v1/contents                阶段 A：selected topic → canonical 长文
                                     body: {topic_id: str} → 201 + content_dict
POST /api/v1/contents/{id}/derivative        阶段 B：单条 → 小红书 slides
                                              → 200 + {derivative: {...}}
POST /api/v1/contents/{id}/generate-images   阶段 B：单条 → 真实 AI 出图
                                              → 200 + {cover_path, inline_images, cost_usd}
POST /api/v1/contents/{id}/schedule          阶段 D：approved 内容手动排期
                                              → 201 + pub_dict
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Query

from pipeline import db
from pipeline.utils.errors import BudgetExceeded
from pipeline.webui import creation_bridge, deps, derivative_bridge, schedule_bridge
from pipeline.webui.mdrender import md_to_html
from pipeline.webui.serialize import (
    content_dict,
    content_image_urls,
    list_content_files,
    pub_dict,
)

router = APIRouter(tags=["contents"])


@router.get("/contents")
def list_contents(
    status: Optional[str] = Query(None),
    pillar: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    with deps._db() as conn:
        rows = db.list_contents(
            conn, status=status, pillar=pillar, limit=limit, offset=offset,
        )
        total = db.count_contents(conn, status=status, pillar=pillar)
    return {
        "items": [content_dict(c) for c in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/contents/{content_id}")
def get_content_detail(content_id: str) -> dict[str, Any]:
    with deps._db() as conn:
        c = db.get_content(conn, content_id)
        if c is None:
            raise HTTPException(status_code=404, detail={"error": {
                "code": "content_not_found",
                "message": f"content {content_id} not found",
            }})
        pubs = db.get_publications_by_content(conn, content_id)
    # canonical.md → HTML（文件不存在时返回空串）
    canonical_html = ""
    try:
        cp = Path(c.canonical_path)
        if cp.exists():
            canonical_html = md_to_html(cp.read_text(encoding="utf-8"))
    except Exception:
        canonical_html = ""
    return {
        **content_dict(c),
        "canonical_html": canonical_html,
        "files": list_content_files(c),
        "images": content_image_urls(c),
        "publications": [pub_dict(p) for p in pubs],
    }


@router.post("/contents", status_code=201)
def create_content(
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """阶段 A：selected topic → canonical 长文创作。

    body: {"topic_id": "t_xxxx"}
    → 201 + content_dict
    → 400/404/500/503 envelope on error
    """
    topic_id = body.get("topic_id")
    if not topic_id or not isinstance(topic_id, str):
        raise HTTPException(status_code=400, detail={"error": {
            "code": "missing_topic_id",
            "message": "body must contain non-empty 'topic_id' string",
        }})

    # cfg.pillars 必填（create_one 内部留作未来按 pillar 调 prompt）
    cfg, err = deps.get_config()
    if err is not None:
        # config 加载失败 → 500（系统性）
        raise HTTPException(status_code=500, detail={"error": {
            "code": "config_load_failed",
            "message": f"failed to load config: {err}",
        }})
    pillars = list(cfg.pillars) if cfg is not None else []

    with deps._db() as conn:
        try:
            content = creation_bridge.create_for_topic(
                conn, topic_id, pillars=pillars,
            )
        except creation_bridge.TopicNotFoundError as e:
            raise HTTPException(status_code=404, detail={"error": {
                "code": "topic_not_found", "message": str(e),
            }})
        except creation_bridge.TopicStatusError as e:
            raise HTTPException(status_code=400, detail={"error": {
                "code": "topic_wrong_status", "message": str(e),
            }})
        except BudgetExceeded as e:
            # 系统级 → 503（HARD_PARTS §4 成本护栏）
            raise HTTPException(status_code=503, detail={"error": {
                "code": "budget_exceeded",
                "message": str(e),
            }})
        except Exception as e:
            # 兜底：单条失败 → 500（编排层本应不阻断，但单端点场景下
            # 直接返 500 让前端展示错误信息；与 CREATE 阶段「单条 skip
            # 继续」不同——UI 一次只跑一条，不存在「批次」概念）
            raise HTTPException(status_code=500, detail={"error": {
                "code": "create_failed",
                "message": f"{type(e).__name__}: {e}",
            }})

    return content_dict(content)


# ── 阶段 B：单条衍生（小红书 slides） ──────────────────────


@router.post("/contents/{content_id}/derivative")
def derive_xhs_endpoint(content_id: str) -> dict[str, Any]:
    """为单条 content 衍生小红书 slides.json + caption + tags。

    复用 pipeline.creators.derivative.derive_one（限定 xiaohongshu 平台），
    不重写业务逻辑。

    body: {}（无参数）
    → 200 + {"derivative": {slides_count, caption_chars, tags}}
    → 404 content_not_found / 400 wrong_status / 503 budget_exceeded /
      500 derive_failed
    """
    with deps._db() as conn:
        try:
            result = derivative_bridge.derive_xhs_for_content(
                conn, content_id,
            )
        except derivative_bridge.ContentNotFoundError as e:
            raise HTTPException(status_code=404, detail={"error": {
                "code": "content_not_found", "message": str(e),
            }})
        except derivative_bridge.ContentStatusError as e:
            raise HTTPException(status_code=400, detail={"error": {
                "code": "wrong_status", "message": str(e),
            }})
        except BudgetExceeded as e:
            # 系统级 → 503（HARD_PARTS §4 成本护栏）
            raise HTTPException(status_code=503, detail={"error": {
                "code": "budget_exceeded", "message": str(e),
            }})
        except Exception as e:
            # 兜底 → 500（与 create 端点行为一致；UI 一次只跑一条）
            raise HTTPException(status_code=500, detail={"error": {
                "code": "derive_failed",
                "message": f"{type(e).__name__}: {e}",
            }})
    return {"derivative": result}


# ── 阶段 B：真实 AI 出图（cover + inline） ─────────────────


@router.post("/contents/{content_id}/generate-images")
def generate_images_endpoint(content_id: str) -> dict[str, Any]:
    """为单条 content 真实 AI 出图：cover.png + N inline-N.png。

    复用 pipeline.creators.image_gen.generate_image，不重写。
    失败兜底（用户决策）：image provider 不可用 → 503 显式失败，**不静默
    降级到模板渲染**（保持图片真实性的护栏）。

    body: {}（无参数）
    → 200 + {cover_path, inline_images, cost_usd}
    → 404 content_not_found / 400 wrong_status /
      503 image_provider_unavailable / 503 budget_exceeded / 500 image_gen_failed
    """
    with deps._db() as conn:
        try:
            result = derivative_bridge.generate_images_for_content(
                conn, content_id,
            )
        except derivative_bridge.ContentNotFoundError as e:
            raise HTTPException(status_code=404, detail={"error": {
                "code": "content_not_found", "message": str(e),
            }})
        except derivative_bridge.ContentStatusError as e:
            raise HTTPException(status_code=400, detail={"error": {
                "code": "wrong_status", "message": str(e),
            }})
        except derivative_bridge.ImageProviderError as e:
            # provider 不可用 → 503（key 缺 / 4xx / 重试耗尽 / 未初始化）
            raise HTTPException(status_code=503, detail={"error": {
                "code": "image_provider_unavailable", "message": str(e),
            }})
        except BudgetExceeded as e:
            # 系统级 → 503（HARD_PARTS §4 成本护栏）
            raise HTTPException(status_code=503, detail={"error": {
                "code": "budget_exceeded", "message": str(e),
            }})
        except Exception as e:
            # 兜底 → 500（其他异常，如 OSError 文件写盘失败）
            raise HTTPException(status_code=500, detail={"error": {
                "code": "image_gen_failed",
                "message": f"{type(e).__name__}: {e}",
            }})
    return result


# ── 阶段 D：手动排期 ─────────────────────────────────────


@router.post("/contents/{content_id}/schedule", status_code=201)
def schedule_content_endpoint(
    content_id: str,
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """对一条 approved (或 gated) content 手动造一条 queued publication。

    M10-11 阶段 D —— 用户在内容详情页选 platform + account + time
    主动排一条 publication，不依赖 CLI scheduler 自动计划。

    body: {
      "platform": "xiaohongshu" | "x" | "toutiao" | "douyin",
      "account_id": "<该 platform.accounts 中的 id>",
      "scheduled_at": "<ISO8601，未来时间>"
    }
    → 201 + pub_dict
    → 404 content_not_found / 400 wrong_status /
      400 platform_not_configured / 400 account_not_found /
      400 invalid_scheduled_at / 409 duplicate_schedule /
      500 schedule_failed

    复用 pipeline.webui.schedule_bridge.schedule_for_content（薄封装调
    db.insert_publication + UNIQUE 兜底），不重写业务。
    """
    # cfg 必填——platform/account 校验必须真 cfg
    cfg, err = deps.get_config()
    if cfg is None:
        # config 加载失败 → 500（系统性，schedule 无法继续）
        raise HTTPException(status_code=500, detail={"error": {
            "code": "config_load_failed",
            "message": f"failed to load config: {err}",
        }})

    with deps._db() as conn:
        try:
            pub = schedule_bridge.schedule_for_content(
                conn, content_id,
                platform=body.get("platform", ""),
                account_id=body.get("account_id", ""),
                scheduled_at=body.get("scheduled_at", ""),
                cfg_obj=cfg,
            )
        except schedule_bridge.ContentNotFoundError as e:
            raise HTTPException(status_code=404, detail={"error": {
                "code": "content_not_found", "message": str(e),
            }})
        except schedule_bridge.ContentWrongStatusError as e:
            raise HTTPException(status_code=400, detail={"error": {
                "code": "wrong_status", "message": str(e),
            }})
        except schedule_bridge.PlatformNotConfiguredError as e:
            raise HTTPException(status_code=400, detail={"error": {
                "code": "platform_not_configured", "message": str(e),
            }})
        except schedule_bridge.AccountNotFoundError as e:
            raise HTTPException(status_code=400, detail={"error": {
                "code": "account_not_found", "message": str(e),
            }})
        except schedule_bridge.InvalidScheduledAtError as e:
            raise HTTPException(status_code=400, detail={"error": {
                "code": "invalid_scheduled_at", "message": str(e),
            }})
        except schedule_bridge.DuplicateScheduleError as e:
            raise HTTPException(status_code=409, detail={"error": {
                "code": "duplicate_schedule", "message": str(e),
            }})
        except Exception as e:
            # 兜底 → 500（其它异常，如 SQL 完整性错误非 UNIQUE 类）
            raise HTTPException(status_code=500, detail={"error": {
                "code": "schedule_failed",
                "message": f"{type(e).__name__}: {e}",
            }})
    return pub_dict(pub)
