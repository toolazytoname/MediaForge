"""LAZY-88: pending suggestion cards never mutate brand stores."""
from __future__ import annotations

from pipeline import projects, visuals
from pipeline.insights import decide_suggestion, generate_suggestions, load_insights
from pipeline.publishers.capability_registry import get_capability

_NOW = "2026-08-17T10:00:00+00:00"


def test_generate_is_pending_and_accept_does_not_write_protected_stores(tmp_path):
    root = tmp_path / "projects"
    projects.create_project(
        title="复盘", idea="想法", audience="读者", goal="保持品牌",
        voice="克制专业", autonomy="collaborate", now=_NOW,
        project_id="prj_insight", projects_root=root,
    )
    visuals.save_plan(
        "prj_insight",
        bible={"style": "plain", "palette": "ink"},
        slots=[{"id": "vsl_cover", "purpose": "封面", "paragraph_anchor": None, "direction": "克制", "aspect_ratio": "16:9"}],
        projects_root=root,
    )
    bible_before = dict(visuals.load_visuals("prj_insight", projects_root=root).bible)
    project_before = projects.load_project("prj_insight", projects_root=root)
    bili_before = get_capability("bilibili")

    board = generate_suggestions("prj_insight", now=_NOW, projects_root=root, actor="cron")
    pending = [item for item in board.suggestions if item.status == "pending"]
    assert pending
    assert {item.kind for item in pending} >= {"visual_bible", "brand_rule", "capability"}
    assert all(item.status == "pending" for item in pending)

    assert visuals.load_visuals("prj_insight", projects_root=root).bible == bible_before
    after_gen = projects.load_project("prj_insight", projects_root=root)
    assert (after_gen.voice, after_gen.goal) == (project_before.voice, project_before.goal)
    bili_after_gen = get_capability("bilibili")
    assert bili_after_gen.delivery.direct is False
    assert bili_after_gen.official_api is False
    assert bili_after_gen.lane == bili_before.lane

    visual = next(item for item in pending if item.kind == "visual_bible")
    decided = decide_suggestion(
        "prj_insight", visual.id, accepted=True, actor="lazy",
        now="2026-08-17T10:05:00+00:00", projects_root=root,
    )
    accepted = next(item for item in decided.suggestions if item.id == visual.id)
    assert accepted.status == "accepted"
    persisted = load_insights("prj_insight", projects_root=root)
    assert any(item.id == visual.id and item.status == "accepted" for item in persisted.suggestions)

    assert visuals.load_visuals("prj_insight", projects_root=root).bible == bible_before
    after_accept = projects.load_project("prj_insight", projects_root=root)
    assert (after_accept.voice, after_accept.goal) == (project_before.voice, project_before.goal)
    assert get_capability("bilibili").delivery.direct is False
    assert get_capability("bilibili").official_api is False
