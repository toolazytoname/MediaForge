"""DL-01: master Markdown export keeps relative image links and is version-safe."""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from pipeline import master_documents, project_exports, projects, visuals


def _project(root: Path, project_id: str = "prj_md_export") -> str:
    projects.create_project(
        title="普通人的 AI 实验室",
        idea="工具越多，越该先问它帮我完成了什么。",
        audience="独立创作者",
        goal="完成一篇可阅读的主稿",
        voice="清楚、克制",
        autonomy="collaborate",
        now="2026-08-13T10:00:00+00:00",
        project_id=project_id,
        projects_root=root,
    )
    return project_id


def _write_png(root: Path, project_id: str, asset_id: str) -> Path:
    path = root / project_id / "assets" / f"{asset_id}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + asset_id.encode("utf-8"))
    return path


def _master_with_images(root: Path, project_id: str) -> None:
    cover = "vas_cover_cn"
    insert = "vas_insert_cn"
    _write_png(root, project_id, cover)
    _write_png(root, project_id, insert)
    visuals.save_plan(
        project_id,
        bible={"style": "editorial"},
        slots=[
            {"id": "vsl_cover", "purpose": "封面", "paragraph_anchor": None, "direction": "封面", "aspect_ratio": "16:9"},
            {"id": "vsl_one", "purpose": "正文插图", "paragraph_anchor": "真实问题", "direction": "插图", "aspect_ratio": "16:9"},
        ],
        projects_root=root,
    )
    for asset_id, slot_id, prompt in (
        (cover, "vsl_cover", "封面"),
        (insert, "vsl_one", "插图"),
    ):
        visuals.record_asset(
            project_id, slot_id=slot_id, prompt=prompt, model="local-import", size="16:9",
            cost_usd=0, now="2026-08-13T10:01:00+00:00", file_path=f"assets/{asset_id}.png",
            status="candidate", asset_id=asset_id, projects_root=root,
        )
        visuals.select_asset(project_id, asset_id, reason="可导出", rating=5, projects_root=root)
    body = (
        f"![封面](/output/projects/{project_id}/assets/{cover}.png)\n\n"
        "## 真实问题\n\n"
        "这是经过确认的正文。\n\n"
        f"![正文插图](/output/projects/{project_id}/assets/{insert}.png)\n\n"
        "> 引用仍应保留。\n"
    )
    master_documents.save_manual(
        project_id,
        title="为什么工具越多反而更忙",
        body=body,
        now="2026-08-13T10:02:00+00:00",
        projects_root=root,
    )


def test_markdown_export_rewrites_web_image_paths_to_relative_assets(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    project_id = _project(root)
    _master_with_images(root, project_id)

    result = project_exports.create_markdown_export(project_id, projects_root=root)

    assert result.kind == "markdown"
    assert result.file_name.startswith("article-m")
    assert result.file_name.endswith(".zip")
    archive = root / project_id / result.path
    assert archive.is_file()
    with zipfile.ZipFile(archive) as package:
        names = set(package.namelist())
        assert "article.md" in names
        assert "assets/vas_cover_cn.png" in names
        assert "assets/vas_insert_cn.png" in names
        markdown = package.read("article.md").decode("utf-8")
    assert "# 为什么工具越多反而更忙" in markdown
    assert f"/output/projects/{project_id}/" not in markdown
    assert "![封面](assets/vas_cover_cn.png)" in markdown
    assert "![正文插图](assets/vas_insert_cn.png)" in markdown
    assert "清楚、克制" in markdown or "个人创作" in markdown
    assert "AI" in markdown


def test_markdown_export_is_idempotent_for_same_master_version(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    project_id = _project(root)
    _master_with_images(root, project_id)

    first = project_exports.create_markdown_export(project_id, projects_root=root)
    path = root / project_id / first.path
    first_mtime = path.stat().st_mtime_ns

    second = project_exports.create_markdown_export(project_id, projects_root=root)

    assert second.file_name == first.file_name
    assert path.stat().st_mtime_ns == first_mtime


def test_markdown_export_versions_when_master_changes(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    project_id = _project(root)
    _master_with_images(root, project_id)
    first = project_exports.create_markdown_export(project_id, projects_root=root)

    master_documents.save_manual(
        project_id,
        title="为什么工具越多反而更忙",
        body="改过的正文，没有图片。\n",
        now="2026-08-13T11:00:00+00:00",
        projects_root=root,
    )
    second = project_exports.create_markdown_export(project_id, projects_root=root)

    assert first.file_name != second.file_name
    assert (root / project_id / first.path).is_file()
    assert (root / project_id / second.path).is_file()


def test_markdown_export_supports_chinese_title_inside_package(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    project_id = _project(root, "prj_cn_name")
    master_documents.save_manual(
        project_id,
        title="中文标题：可重新打开",
        body="没有配图的短文。\n",
        now="2026-08-13T10:02:00+00:00",
        projects_root=root,
    )

    result = project_exports.create_markdown_export(project_id, projects_root=root)
    with zipfile.ZipFile(root / project_id / result.path) as package:
        markdown = package.read("article.md").decode("utf-8")
    assert "中文标题：可重新打开" in markdown


def test_markdown_export_rejects_missing_master(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    project_id = _project(root)
    with pytest.raises(project_exports.ProjectExportError, match="master"):
        project_exports.create_markdown_export(project_id, projects_root=root)


def test_markdown_export_rejects_missing_referenced_asset(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    project_id = _project(root)
    master_documents.save_manual(
        project_id,
        title="缺图文章",
        body=f"![封面](/output/projects/{project_id}/assets/vas_missing.png)\n",
        now="2026-08-13T10:02:00+00:00",
        projects_root=root,
    )
    with pytest.raises(project_exports.ProjectExportError, match="missing"):
        project_exports.create_markdown_export(project_id, projects_root=root)


def test_markdown_export_does_not_require_approval_or_variants(tmp_path: Path) -> None:
    """DL-01 must not reuse the old approval-gated ZIP gate for Markdown."""
    root = tmp_path / "projects"
    project_id = _project(root)
    master_documents.save_manual(
        project_id,
        title="无需审批即可导出",
        body="只有主稿。\n",
        now="2026-08-13T10:02:00+00:00",
        projects_root=root,
    )
    result = project_exports.create_markdown_export(project_id, projects_root=root)
    assert result.kind == "markdown"
    assert (root / project_id / result.path).is_file()
