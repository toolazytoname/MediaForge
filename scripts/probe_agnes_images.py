#!/usr/bin/env python3
"""Agnes-AI 文生图接口探测脚本（按 /zh-Hans/docs/agnes-image-21-flash 文档写的）。

目的：发现真实可用的 endpoint / model / 响应格式 / 延迟 / 价格。
执行：set -a; source secrets/agnes.env; set +a; python scripts/probe_agnes_images.py
输出：stdout JSON + scripts/probe_agnes_images.last.json（供后续 image_gen 模块读取）
安全：不打印 API key，所有错误只输出状态码 + 错误文本前 200 字。
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib import request, error

BASE_URL = os.environ.get("AGNES_BASE_URL", "https://apihub.agnes-ai.com/v1").rstrip("/")
API_KEY = os.environ.get("AGNES_API_KEY", "")
OUTPUT = Path(__file__).with_suffix(".last.json")
IMAGE_MODEL = os.environ.get("AGNES_IMAGE_MODEL", "agnes-image-2.0-flash")


def _post(path: str, body: dict, timeout: float = 90.0) -> tuple[int, dict, float]:
    """POST 到 {BASE_URL}{path}，返回 (status, json_or_text_dict, elapsed_seconds)."""
    data = json.dumps(body).encode("utf-8")
    req = request.Request(
        f"{BASE_URL}{path}",
        data=data,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    t0 = time.monotonic()
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
            try:
                return resp.status, json.loads(payload), time.monotonic() - t0
            except json.JSONDecodeError:
                return resp.status, {"_raw": payload[:500].decode("utf-8", "replace")}, time.monotonic() - t0
    except error.HTTPError as e:
        body_bytes = e.read()[:500] if e.fp else b""
        return e.code, {"_http_error": body_bytes.decode("utf-8", "replace")[:300]}, time.monotonic() - t0
    except Exception as e:
        return 0, {"_network_error": f"{type(e).__name__}: {e}"}, time.monotonic() - t0


def main() -> int:
    if not API_KEY:
        print(json.dumps({"ok": False, "error": "AGNES_API_KEY not set"}))
        return 2

    print(f"[probe] base_url = {BASE_URL}", file=sys.stderr)
    print(f"[probe] image model = {IMAGE_MODEL}", file=sys.stderr)
    print(f"[probe] key loaded: {bool(API_KEY)} (len={len(API_KEY)})", file=sys.stderr)

    # 第一步：GET /v1/models 拿全模型列表（顺便确认 image 模型在册）
    print("\n=== Step 1: GET /v1/models ===", file=sys.stderr)
    try:
        req = request.Request(
            f"{BASE_URL}/models",
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        with request.urlopen(req, timeout=15) as resp:
            models_payload = json.loads(resp.read())
    except Exception as e:
        models_payload = {"_error": f"{type(e).__name__}: {e}"}

    model_names: list[str] = []
    if isinstance(models_payload, dict) and "data" in models_payload:
        for m in models_payload["data"]:
            if isinstance(m, dict) and "id" in m:
                model_names.append(m["id"])
    image_models = [n for n in model_names if "image" in n.lower() or "agnes-image" in n.lower()]
    print(f"[probe] total models = {len(model_names)}", file=sys.stderr)
    print(f"[probe] image models on list = {image_models}", file=sys.stderr)

    # 第二步：按文档真实规范 POST /v1/images/generations
    # 文生图 + URL 输出（避免 b64 长字符串干扰诊断）
    print("\n=== Step 2: POST /v1/images/generations (text-to-image, URL output) ===", file=sys.stderr)
    test_prompt = (
        "a minimal test image: a single red circle on white background, "
        "flat design, no text, no border, simple geometric composition"
    )
    status, body, elapsed = _post("/images/generations", {
        "model": IMAGE_MODEL,
        "prompt": test_prompt,
        "size": "1024x1024",
        "extra_body": {"response_format": "url"},
    })
    print(f"[probe] status={status} elapsed={elapsed:.2f}s", file=sys.stderr)
    t2i_result = {"model": IMAGE_MODEL, "status": status, "elapsed_s": round(elapsed, 2), "response": body}

    # 如果 URL 模式成功，再测 b64 模式（确认两边都通）
    b64_result = None
    if status == 200 and isinstance(body, dict) and body.get("data") and body["data"][0].get("url"):
        print("\n=== Step 3: POST /v1/images/generations (text-to-image, b64 output) ===", file=sys.stderr)
        status2, body2, elapsed2 = _post("/images/generations", {
            "model": IMAGE_MODEL,
            "prompt": test_prompt,
            "size": "1024x1024",
            "return_base64": True,
        })
        print(f"[probe] status={status2} elapsed={elapsed2:.2f}s", file=sys.stderr)
        b64_size = 0
        if isinstance(body2, dict) and body2.get("data") and body2["data"][0].get("b64_json"):
            b64_size = len(body2["data"][0]["b64_json"])
        b64_result = {
            "model": IMAGE_MODEL, "status": status2, "elapsed_s": round(elapsed2, 2),
            "b64_len": b64_size, "response_first_200": (
                json.dumps(body2, ensure_ascii=False)[:200] if isinstance(body2, dict) else str(body2)[:200]
            ),
        }

    # 汇总
    ok = t2i_result["status"] == 200
    result = {
        "ok": ok,
        "probed_at": datetime.now(timezone.utc).isoformat(),
        "base_url": BASE_URL,
        "model": IMAGE_MODEL,
        "all_models": model_names,
        "image_models_listed": image_models,
        "t2i_url": t2i_result,
        "t2i_b64": b64_result,
        "conclusion": (
            "OK: agnes-image-2.1-flash 文生图接口可用"
            if ok else
            "FAILED: 文生图接口不可用，看 response 字段诊断"
        ),
    }
    print("\n=== RESULT ===")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[probe] saved to {OUTPUT}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())