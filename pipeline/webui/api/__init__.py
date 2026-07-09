"""M10-4 /api/v1 只读 API 包。

约定：
  - prefix='/api/v1' 在 app.py 里挂载（app.include_router）
  - 所有路由仅 GET，handler 走 db.py / db_reads.py / serialize.py
  - 错误统一 `{error:{code,message}}` envelope + 对应 HTTP 码
  - 不写库——所有写操作仍走 htmx 路由或（未来）专门的写端点
"""
from fastapi import APIRouter

from pipeline.webui.api import (
    contents,
    dashboard,
    review,
    sources,
    topics,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(dashboard.router)
api_router.include_router(topics.router)
api_router.include_router(sources.router)
api_router.include_router(contents.router)
api_router.include_router(review.router)

__all__ = ["api_router"]
