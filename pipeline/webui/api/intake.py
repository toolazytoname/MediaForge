"""Prepare a messy writing dump and extract sources. Titles come after the article."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from pipeline import intake


router = APIRouter(tags=["intake"])


def _error(status: int, code: str, error: Exception | str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": {"code": code, "message": str(error)}})


def _source_dict(source: intake.IntakeSource) -> dict[str, Any]:
    return {
        "kind": source.kind,
        "title": source.title,
        "reference": source.reference,
        "excerpt": source.excerpt,
        "failure": source.failure,
    }


def _read_uploads(files: list[UploadFile] | None) -> list[tuple[str, bytes]]:
    uploads: list[tuple[str, bytes]] = []
    for item in files or []:
        filename = item.filename or "untitled.txt"
        data = item.file.read()
        uploads.append((filename, data))
    return uploads


@router.post("/intake/prepare")
async def prepare_intake(
    idea: str = Form(""),
    urls: list[str] = Form(default=[]),
    files: list[UploadFile] | None = File(None),
) -> dict[str, Any]:
    try:
        cleaned, sources = intake.collect_sources(
            idea=idea or "",
            urls=urls or [],
            files=_read_uploads(files),
        )
    except intake.IntakeError as error:
        raise _error(400, "invalid_intake", error) from error
    return {
        "idea": cleaned,
        "titles": [],
        "sources": [_source_dict(item) for item in sources],
    }
