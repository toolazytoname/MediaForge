#!/usr/bin/env python3
"""MiniMax 文生图接口探测脚本（按 platform.minimaxi.com/docs/guides/image-generation 文档写的）。

目的：发现真实可用的 endpoint / model / 响应格式 / 延迟 / 价格。
执行：set -a; source secrets/minimax.env; set +a; python scripts/probe_minimax_images.py
       （或 MINIMAX_API_KEY=... python scripts/probe_minimax_images.py）
输出：stdout JSON + scripts/probe_minimax_images.last.json（供 image_gen 模块读取）
安全：不打印 API key，仅输出长度和状态码。
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib import request, error

BASE_URL = os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1").rstrip("/")
API_KEY = os.environ.get("MINIMAX_API_KEY", "")
OUTPUT = Path(__file__).with_suffix(".last.json")
IMAGE_MODEL = "image-01"


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
        return e.code, {"_http_error": body_bytes.decode("utf-8", "replace")[:400]}, time.monotonic() - t0
    except Exception as e:
        return 0, {"_network_error": f"{type(e).__name__}: {e}"}, time.monotonic() - t0


def main() -> int:
    if not API_KEY:
        print(json.dumps({"ok": False, "error": "MINIMAX_API_KEY not set"}))
        return 2

    print(f"[probe] base_url = {BASE_URL}", file=sys.stderr)
    print(f"[probe] image model = {IMAGE_MODEL}", file=sys.stderr)
    print(f"[probe] key loaded: {bool(API_KEY)} (len={len(API_KEY)})", file=sys.stderr)

    # 文生图（按文档：endpoint=/image_generation, aspect_ratio 而非 size）
    print("\n=== Step 1: POST /v1/image_generation (t2i, aspect_ratio=16:9) ===", file=sys.stderr)
    test_prompt = (
        "a minimal test image: a single red circle on white background, "
        "flat design, no text, no border, simple geometric composition"
    )
    status, body, elapsed = _post("/image_generation", {
        "model": IMAGE_MODEL,
        "prompt": test_prompt,
        "aspect_ratio": "16:9",
        "response_format": "base64",
    })
    print(f"[probe] status={status} elapsed={elapsed:.2f}s", file=sys.stderr)

    # 解析 image_base64
    b64_count = 0
    b64_total_len = 0
    first_b64_preview = None
    if isinstance(body, dict):
        data = body.get("data") or {}
        if isinstance(data, dict):
            images = data.get("image_base64")
            if isinstance(images, list):
                b64_count = len(images)
                b64_total_len = sum(len(x) for x in images if isinstance(x, str))
                if b64_count > 0 and isinstance(images[0], str):
                    first_b64_preview = images[0][:60] + "..."  # 仅头 60 字符，不打完整 key
    print(f"[probe] b64 images: count={b64_count}, total_chars={b64_total_len}", file=sys.stderr)

    # 1:1（小红书/头条插图常用方形）
    print("\n=== Step 2: POST /v1/image_generation (t2i, aspect_ratio=1:1) ===", file=sys.stderr)
    status_1x1, body_1x1, elapsed_1x1 = _post("/image_generation", {
        "model": IMAGE_MODEL,
        "prompt": test_prompt,
        "aspect_ratio": "1:1",
        "response_format": "base64",
    })
    print(f"[probe] status={status_1x1} elapsed={elapsed_1x1:.2f}s", file=sys.stderr)

    # 汇总
    ok = status == 200 and b64_count > 0
    result = {
        "ok": ok,
        "probed_at": datetime.now(timezone.utc).isoformat(),
        "base_url": BASE_URL,
        "model": IMAGE_MODEL,
        "endpoint": "/image_generation",
        "t2i_16x9": {
            "status": status,
            "elapsed_s": round(elapsed, 2),
            "b64_count": b64_count,
            "b64_total_chars": b64_total_len,
            "first_b64_head": first_b64_preview,
            "response_keys": list(body.keys()) if isinstance(body, dict) else None,
            "response_data_keys": (
                list(body.get("data", {}).keys())
                if isinstance(body, dict) and isinstance(body.get("data"), dict)
                else None
            ),
            "_raw_error_if_any": body if status != 200 else None,
        },
        "t2i_1x1": {
            "status": status_1x1,
            "elapsed_s": round(elapsed_1x1, 2),
            "_raw_error_if_any": body_1x1 if status_1x1 != 200 else None,
        },
        "conclusion": (
            "OK: image-01 文生图接口可用，返回 data.image_base64 list"
            if ok else
            "FAILED: 看 t2i_16x9._raw_error_if_any 诊断"
        ),
    }
    print("\n=== RESULT ===")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[probe] saved to {OUTPUT}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())