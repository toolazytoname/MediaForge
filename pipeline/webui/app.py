"""M3-3 → M10-9 Web 控制台。

M10 P1 完成: SPA + /api/v1 已上线,旧 htmx 页面路由已移除,这些路径
现在全部交给 SPA catch-all 服务 `frontend/dist/index.html`,前端 Vue
Router 渲染并通过 `/api/v1` 取数据。

写操作路由（promote/reject/approve/reschedule/cancel/retry）保留,
旧版契约不变,curl/脚本可触发;直到 M10 P2 在 SPA 上接线 UI 按钮。

设计原则:
  - UI 不直接写 SQL——读走 db 查询函数,写走 db.transition() 状态机
  - 写操作受三重锁（publish.enabled）约束
  - SPA catch-all 紧跟具体路径注册顺序

路由契约（TECH_SPEC §7 + M10-4/M10-5）:
  GET  /                              SPA catch-all → index.html
  GET  /api/v1/*                      JSON API
  GET  /api/status                    旧版 JSON 状态计数（迁移期保留）
  POST /topics/{id}/promote           scored→selected
  POST /topics/{id}/reject            →rejected
  POST /review/{content_id}           body: {decision, reason?}
  POST /publications/{id}/reschedule  queued 仅可改时间
  POST /publications/{id}/cancel
  POST /publications/{id}/retry       failed→queued（不调 publish）
  GET  /output/<path>                 只读图卡
  GET  /assets/<path>                 SPA Vite 静态资源
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from pipeline import db
from pipeline.models import ContentStatus, PublicationStatus, TopicStatus
from pipeline.webui import deps
from pipeline.webui.api import api_router


# ── 工具 ────────────────────────────────────────────────────


def _status_counts(conn: sqlite3.Connection) -> dict:
    """各状态计数（/api/status 用,保留旧契约兼容）。"""
    out = {"topics": {}, "contents": {}, "publications": {}}
    for table in ("topics", "contents", "publications"):
        rows = conn.execute(
            f"SELECT status, COUNT(*) as n FROM {table} GROUP BY status"
        ).fetchall()
        out[table] = {r["status"]: r["n"] for r in rows}
    return out


def _alert(msg: str) -> HTMLResponse:
    """统一错误片段:role=alert（旧 htmx 客户端期望此格式）。"""
    html = f'<div role="alert" class="alert error">{msg}</div>'
    return HTMLResponse(html, status_code=400)


def _ok(html: str) -> HTMLResponse:
    return HTMLResponse(html, status_code=200)


# ── app factory ─────────────────────────────────────────────


def create_app() -> FastAPI:
    app = FastAPI(title="MediaForge Console", version="0.3.0")

    # 启动时一次性建表（每请求跑 DDL 是浪费）
    _init_c = db.connect(deps._DB_PATH)
    try:
        db.init_db(_init_c)
    finally:
        _init_c.close()

    # M10-4 / M10-5 /api/v1 JSON API
    app.include_router(api_router)

    # /output 静态目录(只读)—— 启动时 mkdir 后无条件挂载
    output_dir = Path("output")
    output_dir.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/output",
        StaticFiles(directory=str(output_dir)),
        name="output",
    )

    # /assets = SPA 构建产物 frontend/dist/assets（Vite 输出）——不存在则跳过
    assets_dir = Path(__file__).parent.parent.parent / "frontend" / "dist" / "assets"
    if assets_dir.is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=str(assets_dir)),
            name="spa-assets",
        )

    # ── 旧 JSON 兼容 + 写操作 POST(契约保留,M10 P2 接到 SPA) ──

    @app.get("/api/status")
    def api_status() -> JSONResponse:
        with deps._db() as conn:
            return JSONResponse(_status_counts(conn))

    @app.post("/topics/{topic_id}/promote", response_class=HTMLResponse)
    def topic_promote(topic_id: str) -> HTMLResponse:
        with deps._db() as conn:
            try:
                db.transition(
                    conn, "topics", topic_id,
                    TopicStatus.SCORED.value,
                    TopicStatus.SELECTED.value,
                )
                return _ok('<span class="badge ok">promoted → selected</span>')
            except Exception as e:
                return _alert(f"promote 失败：{e}")

    @app.post("/topics/{topic_id}/reject", response_class=HTMLResponse)
    def topic_reject(topic_id: str) -> HTMLResponse:
        with deps._db() as conn:
            try:
                db.transition(
                    conn, "topics", topic_id,
                    TopicStatus.SCORED.value,
                    TopicStatus.REJECTED.value,
                )
                return _ok('<span class="badge rejected">rejected</span>')
            except Exception as e:
                return _alert(f"reject 失败：{e}")

    @app.post("/review/{content_id}", response_class=HTMLResponse)
    def review_decide(
        content_id: str,
        decision: str = Form(...),
        reason: str = Form(""),
    ) -> HTMLResponse:
        """body: {decision: approve|reject, reason?}"""
        if decision not in ("approve", "reject"):
            return _alert(f"非法 decision: {decision!r}")
        with deps._db() as conn:
            try:
                if decision == "approve":
                    db.transition(
                        conn, "contents", content_id,
                        ContentStatus.GATED.value,
                        ContentStatus.APPROVED.value,
                    )
                    return _ok('<span class="badge ok">approved</span>')
                verdict = f"REJECTED_BY_HUMAN: {reason}".strip()
                n = db.set_gate_verdict(
                    conn, content_id, verdict,
                    expect_status=ContentStatus.GATED.value,
                )
                if n != 1:
                    return _alert("内容状态已变化，无法 reject")
                db.transition(
                    conn, "contents", content_id,
                    ContentStatus.GATED.value,
                    ContentStatus.REJECTED_BY_HUMAN.value,
                )
                return _ok(
                    f'<span class="badge rejected">'
                    f'rejected ({reason or "no reason"})</span>'
                )
            except Exception as e:
                return _alert(f"{decision} 失败：{e}")

    @app.post(
        "/publications/{pub_id}/reschedule", response_class=HTMLResponse
    )
    def pub_reschedule(
        pub_id: str, scheduled_at: str = Form(...)
    ) -> HTMLResponse:
        with deps._db() as conn:
            try:
                n = db.reschedule_publication(
                    conn, pub_id, scheduled_at,
                    expect_status=PublicationStatus.QUEUED.value,
                )
                if n != 1:
                    return _alert(
                        "reschedule 失败：publication 不存在或状态非 queued"
                    )
                return _ok(
                    f'<span class="badge ok">rescheduled → {scheduled_at}</span>'
                )
            except Exception as e:
                return _alert(f"reschedule 失败：{e}")

    @app.post(
        "/publications/{pub_id}/cancel", response_class=HTMLResponse
    )
    def pub_cancel(pub_id: str) -> HTMLResponse:
        with deps._db() as conn:
            try:
                db.transition(
                    conn, "publications", pub_id,
                    PublicationStatus.QUEUED.value,
                    PublicationStatus.CANCELLED.value,
                )
                return _ok('<span class="badge">cancelled</span>')
            except Exception as e:
                return _alert(f"cancel 失败：{e}")

    @app.post(
        "/publications/{pub_id}/retry", response_class=HTMLResponse
    )
    def pub_retry(pub_id: str) -> HTMLResponse:
        """failed→queued —— 只改状态,不调真实 publish。
        三重锁天然生效：实际发布由 `pipeline.run publish` 触发,
        publish.enabled=false 时整体阻断。
        """
        with deps._db() as conn:
            try:
                db.transition(
                    conn, "publications", pub_id,
                    PublicationStatus.FAILED.value,
                    PublicationStatus.QUEUED.value,
                )
                return _ok('<span class="badge ok">retried → queued</span>')
            except Exception as e:
                return _alert(f"retry 失败：{e}")

    # ── SPA catch-all（最后注册;具体路径/api/output/assets 都已匹配） ──
    spa_index = (
        Path(__file__).parent.parent.parent / "frontend" / "dist" / "index.html"
    )

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_catch_all(full_path: str) -> HTMLResponse:
        # 防御:前面的路由已匹配,这里不应该命中这些前缀
        if (full_path.startswith("api/")
                or full_path.startswith("output/")
                or full_path.startswith("assets/")):
            raise HTTPException(status_code=404, detail="Not Found")
        if spa_index.is_file():
            return HTMLResponse(
                spa_index.read_text(encoding="utf-8"),
                status_code=200,
            )
        # dist 缺失（罕见,正常 clone 后默认提交）
        return HTMLResponse(
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<title>MediaForge</title></head><body>"
            "<h1>MediaForge frontend not built</h1>"
            "<p>Run <code>cd frontend && npm ci && npm run build</code>.</p>"
            "</body></html>",
            status_code=200,
        )

    return app


# ── 入口 ──────────────────────────────────────────────────


def main() -> int:
    """启动入口(cmd_webui 调用)。"""
    import uvicorn

    from pipeline.creators import image_gen
    from pipeline.creators import llm as llm_mod

    try:
        cfg = deps.load_config(deps._CONFIG_PATH)
        host = cfg.webui.host
        port = cfg.webui.port
    except Exception:
        host, port = "127.0.0.1", 8787
    # 按 env 选真实 provider（否则全程 MockProvider，衍生/出图必然失败）
    llm_mod.setup_provider_from_env()
    image_gen.setup_provider_from_env()
    uvicorn.run(create_app(), host=host, port=port, log_level="info")
    return 0


__all__ = ["create_app", "main"]
