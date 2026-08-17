"""Video router.

POST /api/v1/contents/{content_id}/video-script   口播稿派生（LLM）
POST /api/v1/video-jobs                             提交视频生成任务
GET  /api/v1/video-jobs/{job_id}                    查询任务状态
POST /api/v1/video-jobs/{job_id}/cancel             取消未完成任务

任务落 durable_jobs；重启后用 request_json + engine 名重建。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException

from pipeline.deliverables import DeliverablesError
from pipeline.utils.errors import BudgetExceeded
from pipeline.webui import deps, video_bridge
from pipeline.webui.api import projects as projects_api

router = APIRouter(tags=["video"])


def _config_or_500():
    cfg, err = deps.get_config()
    if err is not None:
        raise HTTPException(status_code=500, detail={"error": {
            "code": "config_load_failed",
            "message": f"failed to load config: {err}",
        }})
    return cfg


@router.post("/contents/{content_id}/video-script")
def derive_video_script_endpoint(
    content_id: str, body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """LLM 派生口播稿。body: {duration_s: int} → 200 + {script}。"""
    duration_s = body.get("duration_s")
    if not isinstance(duration_s, int) or duration_s <= 0:
        raise HTTPException(status_code=400, detail={"error": {
            "code": "missing_duration_s",
            "message": "body must contain positive int 'duration_s'",
        }})
    cfg = _config_or_500()
    with deps._db() as conn:
        try:
            script = video_bridge.derive_video_script(
                conn, cfg, content_id, duration_s,
            )
        except video_bridge.ContentNotFoundError as e:
            raise HTTPException(status_code=404, detail={"error": {
                "code": "content_not_found", "message": str(e),
            }})
        except video_bridge.ContentStatusError as e:
            raise HTTPException(status_code=400, detail={"error": {
                "code": "wrong_status", "message": str(e),
            }})
        except BudgetExceeded as e:
            raise HTTPException(status_code=503, detail={"error": {
                "code": "budget_exceeded", "message": str(e),
            }})
        except Exception as e:
            raise HTTPException(status_code=500, detail={"error": {
                "code": "video_script_failed",
                "message": f"{type(e).__name__}: {e}",
            }})
    return {"script": script}


@router.post("/video-jobs", status_code=201)
def submit_video_job_endpoint(
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """提交视频生成任务。

    body: {content_id, engine, script, duration_s, aspect, style,
           idempotency_key?, project_id?, deliverable_id?}
    → 201 + job dict
    """
    content_id = body.get("content_id")
    engine = body.get("engine")
    script = body.get("script")
    duration_s = body.get("duration_s")
    aspect = body.get("aspect")
    style = body.get("style") or {}
    idempotency_key = body.get("idempotency_key")
    project_id = body.get("project_id")
    deliverable_id = body.get("deliverable_id")
    if not isinstance(content_id, str) or not content_id:
        raise HTTPException(status_code=400, detail={"error": {
            "code": "missing_content_id",
            "message": "body must contain non-empty 'content_id' string",
        }})
    if not isinstance(engine, str) or not engine:
        raise HTTPException(status_code=400, detail={"error": {
            "code": "missing_engine",
            "message": "body must contain non-empty 'engine' string",
        }})
    if not isinstance(script, str) or not script:
        raise HTTPException(status_code=400, detail={"error": {
            "code": "missing_script",
            "message": "body must contain non-empty 'script' string",
        }})
    if not isinstance(duration_s, int) or duration_s <= 0:
        raise HTTPException(status_code=400, detail={"error": {
            "code": "missing_duration_s",
            "message": "body must contain positive int 'duration_s'",
        }})
    if not isinstance(aspect, str) or aspect not in ("9:16", "16:9"):
        raise HTTPException(status_code=400, detail={"error": {
            "code": "invalid_aspect",
            "message": "body must contain 'aspect' in {'9:16', '16:9'}",
        }})
    if not isinstance(style, dict):
        raise HTTPException(status_code=400, detail={"error": {
            "code": "invalid_style",
            "message": "'style' must be an object",
        }})
    if idempotency_key is not None and (
        not isinstance(idempotency_key, str) or not idempotency_key
    ):
        raise HTTPException(status_code=400, detail={"error": {
            "code": "invalid_idempotency_key",
            "message": "'idempotency_key' must be a non-empty string",
        }})
    if project_id is not None and (not isinstance(project_id, str) or not project_id):
        raise HTTPException(status_code=400, detail={"error": {
            "code": "invalid_project_id",
            "message": "'project_id' must be a non-empty string",
        }})
    if deliverable_id is not None and (
        not isinstance(deliverable_id, str) or not deliverable_id
    ):
        raise HTTPException(status_code=400, detail={"error": {
            "code": "invalid_deliverable_id",
            "message": "'deliverable_id' must be a non-empty string",
        }})

    cfg = _config_or_500()
    with deps._db() as conn:
        try:
            job = video_bridge.submit_video_job(
                conn, cfg, content_id, engine, script, duration_s,
                aspect, style, idempotency_key=idempotency_key,
                project_id=project_id, deliverable_id=deliverable_id,
                projects_root=projects_api._PROJECTS_ROOT,
            )
        except video_bridge.ContentNotFoundError as e:
            raise HTTPException(status_code=404, detail={"error": {
                "code": "content_not_found", "message": str(e),
            }})
        except video_bridge.ContentStatusError as e:
            raise HTTPException(status_code=400, detail={"error": {
                "code": "wrong_status", "message": str(e),
            }})
        except video_bridge.InvalidEngineError as e:
            raise HTTPException(status_code=400, detail={"error": {
                "code": "invalid_engine", "message": str(e),
            }})
        except video_bridge.EngineUnavailableError as e:
            raise HTTPException(status_code=503, detail={"error": {
                "code": "engine_unavailable", "message": str(e),
            }})
        except video_bridge.CreateError as e:
            raise HTTPException(status_code=400, detail={"error": {
                "code": "create_failed", "message": str(e),
            }})
        except DeliverablesError as e:
            raise HTTPException(status_code=400, detail={"error": {
                "code": "invalid_deliverable", "message": str(e),
            }})
        except BudgetExceeded as e:
            raise HTTPException(status_code=503, detail={"error": {
                "code": "budget_exceeded", "message": str(e),
            }})
        except Exception as e:
            raise HTTPException(status_code=500, detail={"error": {
                "code": "video_submit_failed",
                "message": f"{type(e).__name__}: {e}",
            }})
    return job


@router.get("/video-jobs/{job_id}")
def get_video_job_endpoint(job_id: str) -> dict[str, Any]:
    """查询视频生成任务状态。"""
    cfg = _config_or_500()
    with deps._db() as conn:
        try:
            job = video_bridge.poll_video_job(conn, cfg, job_id)
        except video_bridge.JobNotFoundError as e:
            raise HTTPException(status_code=404, detail={"error": {
                "code": "job_not_found", "message": str(e),
            }})
        except video_bridge.EngineUnavailableError as e:
            raise HTTPException(status_code=503, detail={"error": {
                "code": "engine_unavailable", "message": str(e),
            }})
        except Exception as e:
            raise HTTPException(status_code=500, detail={"error": {
                "code": "video_poll_failed",
                "message": f"{type(e).__name__}: {e}",
            }})
    return job


@router.post("/video-jobs/{job_id}/cancel")
def cancel_video_job_endpoint(job_id: str) -> dict[str, Any]:
    """Cancel a non-terminal durable video job. No further billing or asset write."""
    with deps._db() as conn:
        try:
            job = video_bridge.cancel_video_job(conn, job_id)
        except video_bridge.JobNotFoundError as e:
            raise HTTPException(status_code=404, detail={"error": {
                "code": "job_not_found", "message": str(e),
            }})
        except Exception as e:
            raise HTTPException(status_code=500, detail={"error": {
                "code": "video_cancel_failed",
                "message": f"{type(e).__name__}: {e}",
            }})
    return job
