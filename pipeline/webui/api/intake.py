"""Prepare a messy writing dump: extract sources and propose titles."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from pipeline import intake
from pipeline.creators import llm
from pipeline.webui import deps


router = APIRouter(tags=["intake"])


def _error(status: int, code: str, error: Exception | str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": {"code": code, "message": str(error)}})


def _llm_is_configured() -> bool:
    return not isinstance(llm._PROVIDER, llm.MockProvider)


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


def _propose_titles(idea: str, sources: tuple[intake.IntakeSource, ...]) -> list[str]:
    fallback = intake.heuristic_titles(idea, [item.title for item in sources])
    if not _llm_is_configured():
        return fallback
    try:
        conn = deps.get_conn()
        try:
            return llm.complete_json(
                intake.title_prompt(idea, sources),
                stage="intake_titles",
                ref_id=None,
                model_tier="cheap",
                max_tokens=800,
                conn=conn,
                parse=intake.parse_titles,
            )
        finally:
            conn.close()
    except Exception:
        return fallback


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
    titles = _propose_titles(cleaned, sources)
    return {
        "idea": cleaned,
        "titles": titles,
        "sources": [_source_dict(item) for item in sources],
    }
