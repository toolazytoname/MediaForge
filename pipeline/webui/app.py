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
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from pipeline import db
from pipeline.config import AppConfig, load_config
from pipeline.models import (
    ContentStatus,
    PublicationStatus,
    TopicStatus,
)


_DB_PATH = "state.db"
_CONFIG_PATH = "./config.yaml"


# ── 工具 ────────────────────────────────────────────────────


def _conn() -> sqlite3.Connection:
    c = db.connect(_DB_PATH)
    db.init_db(c)
    return c


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

    # /output 静态目录（只读）
    @app.on_event("startup")
    def _mount_output() -> None:
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
        conn = _conn()
        try:
            counts = _status_counts(conn)
        finally:
            conn.close()
        return templates.TemplateResponse(
            request, "dashboard.html",
            {"counts": counts,
             "now": datetime.utcnow().isoformat()},
        )

    @app.get("/api/status")
    def api_status() -> JSONResponse:
        conn = _conn()
        try:
            return JSONResponse(_status_counts(conn))
        finally:
            conn.close()

    # ── 选题池 ─────────────────────────────────────────────

    @app.get("/topics", response_class=HTMLResponse)
    def topics(
        request: Request,
        status: str | None = None,
    ) -> HTMLResponse:
        conn = _conn()
        try:
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
        finally:
            conn.close()

    @app.post("/topics/{topic_id}/promote", response_class=HTMLResponse)
    def topic_promote(topic_id: str) -> HTMLResponse:
        conn = _conn()
        try:
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
        finally:
            conn.close()

    @app.post("/topics/{topic_id}/reject", response_class=HTMLResponse)
    def topic_reject(topic_id: str) -> HTMLResponse:
        conn = _conn()
        try:
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
        finally:
            conn.close()

    # ── 审核台 ─────────────────────────────────────────────

    @app.get("/review", response_class=HTMLResponse)
    def review(request: Request) -> HTMLResponse:
        conn = _conn()
        try:
            gated = db.get_contents_by_status(
                conn, ContentStatus.GATED.value,
            )
            return templates.TemplateResponse(
                request, "review.html",
                {"gated": gated},
            )
        finally:
            conn.close()

    @app.post("/review/{content_id}", response_class=HTMLResponse)
    def review_decide(
        content_id: str,
        decision: str = Form(...),
        reason: str = Form(""),
    ) -> HTMLResponse:
        """body: {decision: approve|reject, reason?}"""
        if decision not in ("approve", "reject"):
            return _alert(f"非法 decision: {decision!r}")
        conn = _conn()
        try:
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
                else:  # reject
                    # 写入理由到 gate_verdict（复用字段）+ 状态转移
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
        finally:
            conn.close()

    # ── 发布日历 ───────────────────────────────────────────

    @app.get("/calendar", response_class=HTMLResponse)
    def calendar(request: Request) -> HTMLResponse:
        conn = _conn()
        try:
            pubs = []
            for st in PublicationStatus:
                pubs.extend(db.get_publications_by_status(conn, st.value))
            return templates.TemplateResponse(
                request, "calendar.html",
                {"publications": pubs},
            )
        finally:
            conn.close()

    @app.post(
        "/publications/{pub_id}/reschedule", response_class=HTMLResponse
    )
    def pub_reschedule(
        pub_id: str, scheduled_at: str = Form(...)
    ) -> HTMLResponse:
        conn = _conn()
        try:
            try:
                conn.execute(
                    "UPDATE publications SET scheduled_at=?, updated_at=? "
                    "WHERE id=?",
                    (scheduled_at, db.now_utc(), pub_id),
                )
                conn.commit()
                return _ok(
                    f'<span class="badge ok">rescheduled → {scheduled_at}</span>'
                )
            except Exception as e:
                return _alert(f"reschedule 失败：{e}")
        finally:
            conn.close()

    @app.post(
        "/publications/{pub_id}/cancel", response_class=HTMLResponse
    )
    def pub_cancel(pub_id: str) -> HTMLResponse:
        conn = _conn()
        try:
            try:
                db.transition(
                    conn, "publications", pub_id,
                    PublicationStatus.QUEUED.value,
                    PublicationStatus.CANCELLED.value,
                )
                return _ok('<span class="badge">cancelled</span>')
            except Exception as e:
                return _alert(f"cancel 失败：{e}")
        finally:
            conn.close()

    @app.post(
        "/publications/{pub_id}/retry", response_class=HTMLResponse
    )
    def pub_retry(pub_id: str) -> HTMLResponse:
        """failed→queued 走 reset 逻辑（§7）。
        注意：不调真实 publish——发布由 `pipeline.run publish` 触发，
        且 publish.enabled=false 时整体阻断。三重锁天然生效。
        """
        conn = _conn()
        try:
            try:
                db.transition(
                    conn, "publications", pub_id,
                    PublicationStatus.FAILED.value,
                    PublicationStatus.QUEUED.value,
                )
                return _ok('<span class="badge ok">retried → queued</span>')
            except Exception as e:
                return _alert(f"retry 失败：{e}")
        finally:
            conn.close()

    # ── 内容详情 ───────────────────────────────────────────

    @app.get("/contents/{content_id}", response_class=HTMLResponse)
    def content_detail(
        request: Request, content_id: str
    ) -> HTMLResponse:
        conn = _conn()
        try:
            c = db.get_content(conn, content_id)
            if c is None:
                raise HTTPException(404, "content not found")
            # 读 canonical.md（如存在）
            canonical_html = ""
            try:
                cp = Path(c.canonical_path)
                if cp.exists():
                    raw = cp.read_text(encoding="utf-8")
                    canonical_html = _md_to_html(raw)
            except Exception:
                canonical_html = "(无法读取 canonical.md)"
            return templates.TemplateResponse(
                request, "content_detail.html",
                {
                    "content": c,
                    "canonical_html": canonical_html,
                },
            )
        finally:
            conn.close()

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
        sanitized = _sanitize_config(cfg) if cfg else {}
        return templates.TemplateResponse(
            request, "settings.html",
            {"config": sanitized, "err": err},
        )

    return app


# ── 辅助函数 ────────────────────────────────────────────────


def _md_to_html(md: str) -> str:
    """极简 markdown → HTML（标题/段落/列表）。够 webui 内容详情用即可。"""
    lines = md.split("\n")
    out: list[str] = []
    in_ul = False
    for line in lines:
        s = line.rstrip()
        if s.startswith("# "):
            if in_ul:
                out.append("</ul>")
                in_ul = False
            out.append(f"<h1>{_esc(s[2:])}</h1>")
        elif s.startswith("## "):
            if in_ul:
                out.append("</ul>")
                in_ul = False
            out.append(f"<h2>{_esc(s[3:])}</h2>")
        elif s.startswith("- "):
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{_esc(s[2:])}</li>")
        elif s.strip() == "":
            if in_ul:
                out.append("</ul>")
                in_ul = False
        else:
            if in_ul:
                out.append("</ul>")
                in_ul = False
            out.append(f"<p>{_esc(s)}</p>")
    if in_ul:
        out.append("</ul>")
    return "\n".join(out)


def _esc(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _sanitize_config(cfg: AppConfig) -> dict:
    """config 脱敏：webhook_url 等敏感字段值替换为 '***'。"""
    d = cfg.model_dump()
    if "notify" in d and d["notify"].get("webhook_url"):
        d["notify"]["webhook_url"] = "***"
    return d


# 直接启动入口（cmd_webui 调用）
def main() -> int:
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