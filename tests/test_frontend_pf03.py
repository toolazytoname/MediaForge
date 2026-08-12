from pathlib import Path


def test_project_material_drawer_exposes_explicit_parse_and_citation() -> None:
    source = (Path(__file__).parents[1] / "frontend/src/views/Projects.vue").read_text(encoding="utf-8")
    assert "/materials/${item.id}/parse" in source
    assert "只会使用已读取的来源" in source
    assert "material-citation" in source
