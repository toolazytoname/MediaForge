from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.creator_materials import (
    CreatorMaterialError,
    add_file_material,
    add_text_material,
    add_url_material,
    attach_draft_materials,
    list_draft_materials,
)


def test_materials_keep_original_source_hash_time_and_partial_failure(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    image = add_file_material(
        "draft_12345678", "photo.png", b"\x89PNG\r\n\x1a\nimage", "image/png", projects_root=root
    )
    notes = add_text_material("draft_12345678", "一些真实观察", projects_root=root)
    bad_url = add_url_material("draft_12345678", "not a url", projects_root=root)

    materials = list_draft_materials("draft_12345678", projects_root=root)
    assert image.status == "ready" and image.sha256 and image.created_at
    assert image.original_name == "photo.png" and image.source == "photo.png"
    assert notes.kind == "text" and notes.status == "ready"
    assert bad_url.status == "failed" and "http" in (bad_url.error or "")
    assert [item.id for item in materials] == [image.id, notes.id, bad_url.id]


@pytest.mark.parametrize(
    ("name", "payload", "mime", "message"),
    [
        ("../escape.md", b"# note", "text/markdown", "filename"),
        ("empty.md", b"   ", "text/markdown", "empty Markdown"),
        ("bad.pdf", b"not a PDF", "application/pdf", "invalid PDF"),
        ("huge.txt", b"x" * (2 * 1024 * 1024 + 1), "text/plain", "too large"),
    ],
)
def test_file_material_rejects_unsafe_or_unreadable_input(
    tmp_path: Path, name: str, payload: bytes, mime: str, message: str
) -> None:
    with pytest.raises(CreatorMaterialError, match=message):
        add_file_material("draft_12345678", name, payload, mime, projects_root=tmp_path / "projects")


def test_materials_dedupe_and_attach_only_selected_records(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    first = add_text_material("draft_12345678", "同一段材料", projects_root=root)
    assert add_text_material("draft_12345678", "同一段材料", projects_root=root).id == first.id
    url = add_url_material("draft_12345678", "https://example.com/a", projects_root=root)

    attached = attach_draft_materials(
        "draft_12345678", "prj_12345678", [first.id], projects_root=root
    )
    assert len(attached) == 1 and attached[0].id == first.id
    assert (root / "prj_12345678" / "materials" / "materials.json").is_file()
    assert not (root / "prj_12345678" / "materials" / "files").exists()
    assert url.id not in {item.id for item in attached}


def test_material_attachment_rejects_other_draft_and_unknown_id(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    foreign = add_text_material("draft_87654321", "别的草稿", projects_root=root)
    with pytest.raises(CreatorMaterialError, match="unknown material"):
        attach_draft_materials("draft_12345678", "prj_12345678", [foreign.id], projects_root=root)
    with pytest.raises(CreatorMaterialError, match="unknown material"):
        attach_draft_materials("draft_12345678", "prj_12345678", ["mat_missing"], projects_root=root)


def test_material_manifest_rejects_unknown_fields_and_unsafe_file_path(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    item = add_text_material("draft_12345678", "一段资料", projects_root=root)
    manifest = root / ".creator-drafts" / "draft_12345678" / "materials" / "materials.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload[0]["unexpected"] = True
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CreatorMaterialError, match="unknown fields"):
        list_draft_materials("draft_12345678", projects_root=root)
    payload[0].pop("unexpected")
    payload[0]["stored_path"] = "../escape"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CreatorMaterialError, match="unsafe"):
        list_draft_materials("draft_12345678", projects_root=root)
    assert item.id
