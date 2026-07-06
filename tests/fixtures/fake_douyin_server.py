"""Fake 抖音 server fixture — 用于 Douyin Publisher 真实 Playwright 端到端冒烟。

模拟 creator.douyin.com 最小子集：
- GET  /creator-micro/home                  → 创作者主页（健康检查）
- GET  /creator-micro/home/upload/video     → 发布页（含 AI 勾选框 + 视频上传）
- POST /upload-handle                       → 视频上传完成回调（mock）
- GET  /creator-micro/content/manage?video_id=xxx → 成功跳转页

AI 勾选框关键：必须包含 PRD §3.4 提到的 input[type='checkbox'][data-type='ai-generated']
或 .ai-declare input[type='checkbox'] 之一，Playwright 才会勾上。
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
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse


PROFILE_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>创作者中心</title></head>
<body>
<header>抖音创作者中心</header>
<main>
  <h1>欢迎回来</h1>
  <p>这里是创作者后台。显示已发布作品列表（mock）。</p>
</main>
</body></html>"""


PUBLISH_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>发布视频</title></head>
<body>
<header>发布视频</header>
<form id="publish-form" method="post" action="/upload-handle">
  <input type="file" accept="video/mp4" name="video" />
  <input type="text" name="title" placeholder="作品标题" maxlength="30" />

  <div class="ai-declare">
    <label>
      <input type="checkbox" data-type="ai-generated" />
      内容含 AI 生成
    </label>
    <select data-type="ai-ratio">
      <option value="low">低</option>
      <option value="medium">中</option>
      <option value="high">高</option>
    </select>
  </div>

  <textarea name="description" placeholder="写点介绍吧"></textarea>
  <button type="submit" class="publish-btn">发布</button>
</form>
</body></html>"""


SUCCESS_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>发布成功</title></head>
<body>
<header>已发布</header>
<main><p>你的视频已提交审核。</p></main>
</body></html>"""


app = FastAPI()


@app.get("/creator-micro/home", response_class=HTMLResponse)
async def home() -> HTMLResponse:
    return HTMLResponse(PROFILE_HTML)


@app.get("/", response_class=HTMLResponse)
async def root() -> HTMLResponse:
    return HTMLResponse(PROFILE_HTML)


@app.get("/creator-micro/home/upload/video", response_class=HTMLResponse)
async def upload_page() -> HTMLResponse:
    return HTMLResponse(PUBLISH_HTML)


@app.post("/upload-handle")
async def upload_handle() -> RedirectResponse:
    video_id = ("71" + secrets.token_hex(4))[:10]
    return RedirectResponse(
        url=f"/creator-micro/content/manage?video_id={video_id}",
        status_code=303,
    )


@app.get("/creator-micro/content/manage", response_class=HTMLResponse)
async def manage(video_id: str = "") -> HTMLResponse:
    return HTMLResponse(
        SUCCESS_HTML.replace("已发布", f"已发布 video_id={video_id}"),
    )


@contextmanager
def start_server_subprocess():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    cmd = [
        sys.executable, "-m", "uvicorn",
        "tests.fixtures.fake_douyin_server:app",
        "--host", "127.0.0.1",
        "--port", str(port),
        "--log-level", "warning",
        "--no-access-log",
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        cwd=str(Path(__file__).parent.parent.parent),
    )

    base_url = f"http://127.0.0.1:{port}"
    ready = False
    with httpx.Client() as client:
        for _ in range(50):
            try:
                r = client.get(f"{base_url}/creator-micro/home", timeout=0.5)
                if r.status_code == 200:
                    ready = True
                    break
            except Exception:
                time.sleep(0.1)
        if not ready:
            proc.terminate()
            proc.wait(timeout=3)
            raise RuntimeError(f"fake douyin server failed to start: {port}")

    try:
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)


__all__ = ["app", "start_server_subprocess"]