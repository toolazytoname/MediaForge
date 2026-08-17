#!/usr/bin/env python3
"""Offline secret scan for the local/CI verify gate.

Does not need real credentials. Flags high-confidence secret material in
tracked (or walkable) files. Dummy test tokens are ignored by pattern length.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SKIP_DIR_NAMES = {
    ".git", ".venv", "venv", "node_modules", "__pycache__",
    ".pytest_cache", "htmlcov",
}

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("openai_proj_key", re.compile(r"sk-proj-[A-Za-z0-9_-]{20,}")),
    ("openai_legacy_key", re.compile(r"sk-[A-Za-z0-9]{32,}")),
    ("github_pat", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("github_token", re.compile(r"ghp_[A-Za-z0-9]{36,}")),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]


def _iter_files() -> list[Path]:
    try:
        out = subprocess.check_output(
            ["git", "ls-files", "-z"], cwd=ROOT, text=False,
        )
        files = [ROOT / Path(p.decode()) for p in out.split(b"\0") if p]
        return [p for p in files if p.is_file()]
    except (OSError, subprocess.CalledProcessError):
        files: list[Path] = []
        for path in ROOT.rglob("*"):
            if not path.is_file():
                continue
            if any(part in SKIP_DIR_NAMES for part in path.parts):
                continue
            files.append(path)
        return files


def main() -> int:
    hits: list[str] = []
    for path in _iter_files():
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".ico"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = path.relative_to(ROOT).as_posix()
        for name, pattern in PATTERNS:
            if pattern.search(text):
                hits.append(f"{rel}: {name}")
    if hits:
        print("secret-scan: FAIL")
        for hit in hits:
            print(f"  {hit}")
        return 1
    print("secret-scan: ok (no high-confidence secrets)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
