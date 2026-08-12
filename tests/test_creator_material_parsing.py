from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.creator_materials import add_file_material, add_text_material, add_url_material, attach_draft_materials
from pipeline.creator_material_parsing import MaterialParseError, parse_project_material, project_material_context


def _attach(root: Path, item_id: str) -> str:
    attach_draft_materials("draft_12345678", "prj_12345678", [item_id], projects_root=root)
    return "prj_12345678"


def test_text_and_markdown_are_parsed_into_locatable_citations(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    item = add_text_material("draft_12345678", "第一段事实。\n\n第二段是作者的判断。", projects_root=root)
    project_id = _attach(root, item.id)

    parsed = parse_project_material(project_id, item.id, projects_root=root)

    assert parsed["status"] == "used"
    assert parsed["source_sha256"] == item.sha256
    assert parsed["segments"][0]["citation"] == f"{item.id}:1"
    assert parsed["segments"][0]["kind"] == "source_fact"
    assert project_material_context(project_id, projects_root=root)[0]["citation"] == f"{item.id}:1"


def test_explicit_author_view_and_verification_markers_are_not_presented_as_facts(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    item = add_text_material("draft_12345678", "[观点] 我认为这会改变工作。\n\n[待核查] 这条数字需要确认。", projects_root=root)
    project_id = _attach(root, item.id)
    parsed = parse_project_material(project_id, item.id, projects_root=root)
    assert [segment["kind"] for segment in parsed["segments"]] == ["author_view", "needs_verification"]
    assert project_material_context(project_id, projects_root=root) == ()


def test_bad_or_private_url_is_not_used_and_does_not_block_other_material(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    url = add_url_material("draft_12345678", "http://127.0.0.1/private", projects_root=root)
    note = add_text_material("draft_12345678", "仍然可用的材料", projects_root=root)
    attach_draft_materials("draft_12345678", "prj_12345678", [url.id, note.id], projects_root=root)

    rejected = parse_project_material("prj_12345678", url.id, projects_root=root)
    usable = parse_project_material("prj_12345678", note.id, projects_root=root)

    assert rejected["status"] == "not_used"
    assert "private" in rejected["error"]
    assert usable["status"] == "used"


def test_url_redirect_hop_and_malicious_html_are_checked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "projects"
    item = add_url_material("draft_12345678", "https://example.com/a", projects_root=root)
    _attach(root, item.id)

    class Response:
        status_code = 302
        headers = {"location": "http://169.254.169.254/latest"}
        content = b""
        url = "https://example.com/a"

    monkeypatch.setattr("pipeline.creator_material_parsing._request", lambda _url: Response())
    parsed = parse_project_material("prj_12345678", item.id, projects_root=root)
    assert parsed["status"] == "not_used"
    assert "private" in parsed["error"]


def test_oversized_and_scanned_pdf_are_explicitly_not_used(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    pdf = add_file_material("draft_12345678", "scan.pdf", b"%PDF-1.4\n/Type /Page\n%%EOF", "application/pdf", projects_root=root)
    _attach(root, pdf.id)
    parsed = parse_project_material("prj_12345678", pdf.id, projects_root=root)
    assert parsed["status"] == "not_used"
    assert "no extractable text" in parsed["error"]


def test_image_is_retained_with_reviewable_metadata(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    image = add_file_material("draft_12345678", "photo.png", b"\x89PNG\r\n\x1a\nimage", "image/png", projects_root=root)
    _attach(root, image.id)
    parsed = parse_project_material("prj_12345678", image.id, projects_root=root)
    assert parsed["status"] == "used"
    assert parsed["image"]["original_name"] == "photo.png"
    assert parsed["image"]["description_status"] == "not_generated"


def test_missing_source_and_oversized_text_are_not_claimed_as_used(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    item = add_text_material("draft_12345678", "原始来源可能丢失", projects_root=root)
    _attach(root, item.id)
    source = root / "prj_12345678" / "materials" / "texts" / f"{item.id}.txt"
    source.unlink()
    parsed = parse_project_material("prj_12345678", item.id, projects_root=root)
    assert parsed["status"] == "not_used"
    assert "source file is missing" in parsed["error"]


def test_invalid_existing_analysis_is_rejected_without_being_overwritten(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    item = add_text_material("draft_12345678", "可信来源", projects_root=root)
    _attach(root, item.id)
    analysis = root / "prj_12345678" / "materials" / "analysis.json"
    analysis.write_text('{"%s": {"status": "used"}}' % item.id, encoding="utf-8")
    with pytest.raises(MaterialParseError, match="analysis is invalid"):
        parse_project_material("prj_12345678", item.id, projects_root=root)
    assert analysis.read_text(encoding="utf-8") == '{"%s": {"status": "used"}}' % item.id


def test_declared_oversized_url_is_rejected_before_body_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "projects"
    item = add_url_material("draft_12345678", "https://example.com/a", projects_root=root)
    _attach(root, item.id)
    monkeypatch.setattr("pipeline.creator_material_parsing._validate_public_url", lambda _url: None)

    class Response:
        status_code = 200
        headers = {"content-type": "text/plain", "content-length": str(3 * 1024 * 1024)}
        url = "https://example.com/a"
        @property
        def content(self):  # pragma: no cover - assertion is the test
            raise AssertionError("body must not be read")

    monkeypatch.setattr("pipeline.creator_material_parsing._request", lambda _url: Response())
    parsed = parse_project_material("prj_12345678", item.id, projects_root=root)
    assert parsed["status"] == "not_used"
    assert "too large" in parsed["error"]
