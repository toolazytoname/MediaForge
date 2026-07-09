"""M10-5 settings router.

GET /api/v1/settings — config 脱敏展示 + doctor 报告合并
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from pipeline import doctor
from pipeline.webui import deps
from pipeline.webui.sanitize import sanitize_config

router = APIRouter(tags=["settings"])


@router.get("/settings")
def get_settings() -> dict[str, Any]:
    """脱敏 config + doctor 体检报告。"""
    cfg, err = deps.get_config()
    if cfg is None:
        return {
            "config": {},
            "config_error": err,
            "doctor": [],
        }
    sanitized = sanitize_config(cfg.model_dump())
    # doctor 用 cfg_path 报告
    try:
        report = doctor.run_doctor(deps._CONFIG_PATH)
    except Exception as e:
        # 兜底：手动造一个失败的 CheckResult
        from pipeline.doctor import CheckResult
        return {
            "config": sanitized,
            "doctor": [{"name": "doctor", "ok": False, "hint": f"doctor 失败：{e}"}],
        }
    return {
        "config": sanitized,
        "doctor": [
            {"name": r.name, "ok": r.ok, "hint": r.hint}
            for r in report
        ],
    }
