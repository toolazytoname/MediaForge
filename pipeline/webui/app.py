"""M3-3 Web 控制台（TECH_SPEC §7 + ARCHITECTURE §3.9）。

设计原则：
  - FastAPI + Jinja2 服务端渲染 + htmx（无 npm/Vite）
  - **UI 不直接写 SQL**——读走 db 查询函数，写走 db.transition() 状态机
  - 所有 POST 返回 htmx 局部片段；错误统一 role=alert 片段
  - 三重锁（publish.enabled）对 UI 触发的发布操作同样生效

路由契约（TECH_SPEC §7）：
  GET  /                     Dashboard
  GET  /api/status           JSON 状态计数
  GET  /topics?status=       选题池
  POST /topics/{id}/promote  scored→selected
  POST /topics/{id}/reject   →rejected
  GET  /review               审核台（gated 卡片流）
  POST /review/{content_id}  body: {decision, reason?}
  GET  /calendar             发布日历
  POST /publications/{id}/reschedule
  POST /publications/{id}/cancel
  POST /publications/{id}/retry        (failed→queued)
  GET  /contents/{id}        内容详情
  GET  /settings             config 脱敏展示
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from pipeline import db
from pipeline.config import load_config
from pipeline.models import ContentStatus, PublicationStatus, TopicStatus
from pipeline.webui.mdrender import md_to_html
from pipeline.webui.sanitize import sanitize_config


_DB_PATH = "state.db"
_CONFIG_PATH = "./config.yaml"


# ── 工具 ────────────────────────────────────────────────────


def _conn() -> sqlite3.Connection:
    return db.connect(_DB_PATH)


@contextmanager
def _db():
    """conn 生命周期 context manager（自动 init + close）。"""
    c = _conn()
    try:
        yield c
    finally:
        c.close()


def _status_counts(conn: sqlite3.Connection) -> dict:
    """各状态计数（dashboard 用）。"""
    out = {"topics": {}, "contents": {}, "publications": {}}
    for table in ("topics", "contents", "publications"):
        rows = conn.execute(
            f"SELECT status, COUNT(*) as n FROM {table} GROUP BY status"
        ).fetchall()
        out[table] = {r["status"]: r["n"] for r in rows}
    return out


def _alert(msg: str) -> HTMLResponse:
    """统一错误片段：role=alert。"""
    html = f'<div role="alert" class="alert error">{msg}</div>'
    return HTMLResponse(html, status_code=400)


def _ok(html: str) -> HTMLResponse:
    return HTMLResponse(html, status_code=200)


# ── app factory ─────────────────────────────────────────────


def create_app() -> FastAPI:
    app = FastAPI(title="MediaForge Console", version="0.3.0")

    base = Path(__file__).parent
    templates = Jinja2Templates(directory=str(base / "templates"))

    # 应用启动时一次性建表（每请求跑 DDL 是浪费；create_app 内做完即可）
    # db.connect 不支持 contextmanager，手动 close
    _init_c = db.connect(_DB_PATH)
    try:
        db.init_db(_init_c)
    finally:
        _init_c.close()

    # /output 静态目录（只读）—— 工厂时挂载（同步，幂等）
    output_dir = Path("output")
    if output_dir.exists():
        app.mount(
            "/output",
            StaticFiles(directory=str(output_dir)),
            name="output",
        )

    # 静态 vendor (pico.css)
    static_dir = base / "static"
    if static_dir.exists():
        app.mount(
            "/static",
            StaticFiles(directory=str(static_dir)),
            name="static",
        )

    # ── Dashboard / API ────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request) -> HTMLResponse:
        with _db() as conn:
            counts = _status_counts(conn)
        return templates.TemplateResponse(
            request, "dashboard.html",
            {"counts": counts,
             "now": datetime.now(timezone.utc).isoformat()},
        )

    @app.get("/api/status")
    def api_status() -> JSONResponse:
        with _db() as conn:
            return JSONResponse(_status_counts(conn))

    # ── 选题池 ─────────────────────────────────────────────

    @app.get("/topics", response_class=HTMLResponse)
    def topics(
        request: Request,
        status: str | None = None,
    ) -> HTMLResponse:
        with _db() as conn:
            if status:
                rows = db.get_topics_by_status(conn, status)
            else:
                rows = []
                for st in TopicStatus:
                    rows.extend(db.get_topics_by_status(conn, st.value))
            return templates.TemplateResponse(
                request, "topics.html",
                {"topics": rows, "filter": status or ""},
            )

    @app.post("/topics/{topic_id}/promote", response_class=HTMLResponse)
    def topic_promote(topic_id: str) -> HTMLResponse:
        with _db() as conn:
            try:
                db.transition(
                    conn, "topics", topic_id,
                    TopicStatus.SCORED.value,
                    TopicStatus.SELECTED.value,
                )
                return _ok(
                    f'<span class="badge ok">promoted → selected</span>'
                )
            except Exception as e:
                return _alert(f"promote 失败：{e}")

    @app.post("/topics/{topic_id}/reject", response_class=HTMLResponse)
    def topic_reject(topic_id: str) -> HTMLResponse:
        with _db() as conn:
            try:
                db.transition(
                    conn, "topics", topic_id,
                    TopicStatus.SCORED.value,
                    TopicStatus.REJECTED.value,
                )
                return _ok(
                    f'<span class="badge rejected">rejected</span>'
                )
            except Exception as e:
                return _alert(f"reject 失败：{e}")

    # ── 审核台 ─────────────────────────────────────────────

    @app.get("/review", response_class=HTMLResponse)
    def review(request: Request) -> HTMLResponse:
        with _db() as conn:
            gated = db.get_contents_by_status(
                conn, ContentStatus.GATED.value,
            )
            return templates.TemplateResponse(
                request, "review.html",
                {"gated": gated},
            )

    @app.post("/review/{content_id}", response_class=HTMLResponse)
    def review_decide(
        content_id: str,
        decision: str = Form(...),
        reason: str = Form(""),
    ) -> HTMLResponse:
        """body: {decision: approve|reject, reason?}"""
        if decision not in ("approve", "reject"):
            return _alert(f"非法 decision: {decision!r}")
        with _db() as conn:
            try:
                if decision == "approve":
                    db.transition(
                        conn, "contents", content_id,
                        ContentStatus.GATED.value,
                        ContentStatus.APPROVED.value,
                    )
                    return _ok(
                        '<span class="badge ok">approved</span>'
                    )
                # reject: 写理由到 gate_verdict + 状态转移
                verdict = f"REJECTED_BY_HUMAN: {reason}".strip()
                cur = conn.execute(
                    "UPDATE contents SET gate_verdict=?, updated_at=? "
                    "WHERE id=? AND status=?",
                    (verdict, db.now_utc(),
                     content_id, ContentStatus.GATED.value),
                )
                conn.commit()
                if cur.rowcount != 1:
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

    # ── 发布日历（M4-4 周视图） ────────────────────────────────

    @app.get("/calendar", response_class=HTMLResponse)
    def calendar(
        request: Request, week: str | None = None,
    ) -> HTMLResponse:
        """周视图日历（htmx 换周）。

        ?week=YYYY-MM-DD 为周锚定日（缺省 = 今天 UTC）。
        hx-get="/calendar?week=..." hx-target="#calendar-grid" hx-swap="innerHTML"
        """
        from pipeline.webui.calendar import bucket_week

        with _db() as conn:
            pubs = []
            for st in PublicationStatus:
                pubs.extend(db.get_publications_by_status(conn, st.value))
            bucket = bucket_week(pubs, anchor_iso=week)
            return templates.TemplateResponse(
                request, "calendar.html",
                {"bucket": bucket, "week": week or bucket.this_week},
            )

    @app.post(
        "/publications/{pub_id}/reschedule", response_class=HTMLResponse
    )
    def pub_reschedule(
        pub_id: str, scheduled_at: str = Form(...)
    ) -> HTMLResponse:
        """reschedule 语义：仅 queued 状态的 publication 可改时间。

        TECH_SPEC §4 没有 reschedule 这条状态边——保留 scheduled_at 字段
        可变但 status 限定为 queued（发布中/已完成/失败/取消 的不能再改）。
        """
        with _db() as conn:
            try:
                cur = conn.execute(
                    "UPDATE publications SET scheduled_at=?, updated_at=? "
                    "WHERE id=? AND status=?",
                    (scheduled_at, db.now_utc(),
                     pub_id, PublicationStatus.QUEUED.value),
                )
                conn.commit()
                if cur.rowcount != 1:
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
        with _db() as conn:
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
        """failed→queued 走 reset 逻辑（§7）。
        注意：不调真实 publish——发布由 `pipeline.run publish` 触发，
        且 publish.enabled=false 时整体阻断。三重锁天然生效。
        """
        with _db() as conn:
            try:
                db.transition(
                    conn, "publications", pub_id,
                    PublicationStatus.FAILED.value,
                    PublicationStatus.QUEUED.value,
                )
                return _ok('<span class="badge ok">retried → queued</span>')
            except Exception as e:
                return _alert(f"retry 失败：{e}")

    # ── 内容详情 ───────────────────────────────────────────

    @app.get("/contents/{content_id}", response_class=HTMLResponse)
    def content_detail(
        request: Request, content_id: str
    ) -> HTMLResponse:
        with _db() as conn:
            c = db.get_content(conn, content_id)
            if c is None:
                raise HTTPException(404, "content not found")
            canonical_html = ""
            try:
                cp = Path(c.canonical_path)
                if cp.exists():
                    canonical_html = md_to_html(cp.read_text(encoding="utf-8"))
            except Exception:
                canonical_html = "(无法读取 canonical.md)"
            return templates.TemplateResponse(
                request, "content_detail.html",
                {"content": c, "canonical_html": canonical_html},
            )

    # ── 设置 ───────────────────────────────────────────────

    @app.get("/settings", response_class=HTMLResponse)
    def settings(request: Request) -> HTMLResponse:
        try:
            cfg = load_config(_CONFIG_PATH)
        except Exception as e:
            cfg = None
            err = str(e)
        else:
            err = None
        # 脱敏：把 webhook_url 等敏感字段值替换为 "***"
        sanitized = sanitize_config(cfg.model_dump()) if cfg else {}
        # cookie 健康状态（轻量级：只校验文件存在 + 格式合法；不实际探活）
        cookie_health = []
        if cfg is not None:
            from pipeline.webui.cookie_health_views import collect_cookie_health
            cookie_health = collect_cookie_health(cfg)
        return templates.TemplateResponse(
            request, "settings.html",
            {
                "config": sanitized,
                "err": err,
                "cookie_health": cookie_health,
            },
        )

    return app


# ── 辅助函数 ────────────────────────────────────────────────


def main() -> int:
    """启动入口（cmd_webui 调用）。"""
    import uvicorn
    try:
        cfg = load_config(_CONFIG_PATH)
        host = cfg.webui.host
        port = cfg.webui.port
    except Exception:
        host, port = "127.0.0.1", 8787
    uvicorn.run(create_app(), host=host, port=port, log_level="info")
    return 0


__all__ = ["create_app", "main"]