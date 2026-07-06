"""Fake 头条 server fixture — 用于 头条 Publisher 真实 Playwright 端到端冒烟。

模拟 头条 mp.toutiao.com 的最小子集：
- GET  /profile              → 创作者主页（健康检查目标）
- GET  /auth/login           → 登录页（含 "扫码登录" 关键字）
- GET  /publish/article      → 发布表单（含标题输入 + 正文编辑器 + 发布按钮）
- POST /publish/article      → 处理发布 → 重定向到 /content/manage?mid=xxx

启动：`start_server_subprocess()` → 子进程 + uvicorn CLI；同步 + 独立事件循环，
避免与 pytest / playwright 的事件循环冲突。
"""
from __future__ import annotations

import secrets
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse


# ── HTML 模板（最小可用；选择器全部命中） ─────────────────


PROFILE_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>创作者中心</title></head>
<body>
<header>欢迎回到头条创作者中心</header>
<main>
  <h1>我的主页</h1>
  <p>这是创作者后台。显示已发表作品列表（mock）。</p>
</main>
</body></html>"""

LOGIN_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>登录</title></head>
<body>
<header>扫码登录</header>
<main>
  <p>请使用手机扫码登录</p>
</main>
</body></html>"""

PUBLISH_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>发布文章</title></head>
<body>
<header>发布文章</header>
<form id="publish-form" method="post" action="/publish/article">
  <input type="text" name="title" placeholder="请输入文章标题" />
  <div class="editor-content" contenteditable="true" data-placeholder="正文">
    文章正文占位符
  </div>
  <input type="radio" name="cover_mode" value="auto" checked />
  <button type="submit" class="publish-btn">发布</button>
</form>
</body></html>"""

# 成功后跳转目标
SUCCESS_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>发布成功</title></head>
<body>
<header>已发布</header>
<main><p>你的文章已发布。</p></main>
</body></html>"""


# ── FastAPI app ────────────────────────────────────────


app = FastAPI()


@app.get("/profile", response_class=HTMLResponse)
async def profile() -> HTMLResponse:
    return HTMLResponse(PROFILE_HTML)


@app.get("/auth/login", response_class=HTMLResponse)
async def login_page() -> HTMLResponse:
    return HTMLResponse(LOGIN_HTML)


@app.get("/publish/article", response_class=HTMLResponse)
async def publish_form() -> HTMLResponse:
    return HTMLResponse(PUBLISH_HTML)


@app.post("/publish/article")
async def publish_submit(title: str = Form(...)) -> RedirectResponse:
    # 生成随机 mid（模仿 mp.toutiao.com/content/manage?mid=7123456789...）
    mid = ("71" + secrets.token_hex(4))[:10]
    return RedirectResponse(
        url=f"/content/manage?mid={mid}",
        status_code=303,
    )


@app.get("/content/manage", response_class=HTMLResponse)
async def content_manage(mid: str = "") -> HTMLResponse:
    # 末尾追加 mid 让浏览器"看到"自己
    return HTMLResponse(
        SUCCESS_HTML.replace("已发布", f"已发布 mid={mid}"),
    )


# ── 测试用启动器（subprocess + uvicorn CLI） ────────────────


@contextmanager
def start_server_subprocess():
    """同步起 fake 头条 server（uvicorn 子进程）。

    返回 base_url。teardown 杀子进程。
    子进程独立事件循环，不与 pytest / playwright 冲突。
    """
    # 找空闲端口
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    # 起 uvicorn 子进程（用 module 路径启动本文件里的 app）
    cmd = [
        sys.executable, "-m", "uvicorn",
        "tests.fixtures.fake_toutiao_server:app",
        "--host", "127.0.0.1",
        "--port", str(port),
        "--log-level", "warning",
        "--no-access-log",
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(Path(__file__).parent.parent.parent),  # 项目根
    )

    base_url = f"http://127.0.0.1:{port}"

    # 等 server ready（最多 5s）
    ready = False
    with httpx.Client() as client:
        for _ in range(50):
            try:
                r = client.get(f"{base_url}/profile", timeout=0.5)
                if r.status_code == 200:
                    ready = True
                    break
            except Exception:
                time.sleep(0.1)
        if not ready:
            proc.terminate()
            proc.wait(timeout=3)
            raise RuntimeError(
                f"fake server failed to start on port {port}; "
                f"cmd={cmd}"
            )

    try:
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)


__all__ = ["app", "start_server_subprocess", "PROFILE_HTML", "LOGIN_HTML", "PUBLISH_HTML"]