"""AUTO-01: real pack create pipeline, not theme-loop placeholder."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pipeline import research
from pipeline.auto_create import AutoCreateError, run_auto_create, score_manuscript
from pipeline.delivery import service as delivery_service
from pipeline.interviews import confirm_interview, save_interview
from pipeline.master_documents import load_master
from pipeline.pack import prepare_pack
from pipeline.projects import create_project, load_project, update_project
from tests.test_autonomy_policy import _project
from tests.test_delivery_kernel import _complete, _ready

NOW = "2026-08-09T00:02:00+00:00"


def _interview(project_id, root, *, book=False):
    sources = [{"kind": "url", "title": "公开报道", "reference": "https://example.com/a", "excerpt": "摘录"}]
    if book:
        sources.append({"kind": "book", "title": "未读书", "reference": "isbn:1", "excerpt": ""})
    save_interview(
        project_id, viewpoint="我的判断是边界比速度重要", motive="想写给同行看",
        experience="我在一次延期里改过排期", sources=sources, now=NOW, projects_root=root,
    )
    confirm_interview(project_id, now=NOW, projects_root=root)


def _source(project_id, root):
    research.add_source(
        project_id, title="公开报道", reference="https://example.com/a",
        summary="只核实过的公开事实", now=NOW, projects_root=root,
    )


_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc`\x00\x00"
    b"\x00\x02\x00\x01\xe5'\xde\xfc\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _draft_fn(project, interview, board, critique=None):
    source = board.sources[0]
    parts = [
        interview.viewpoint, interview.motive, interview.experience,
        f"根据来源《{source.title}》，{source.summary}。",
    ]
    extras = [
        "把验收标准写进纪要，而不是口头承诺下周再看。",
        "来源摘要只核实过的公开事实，不能扩写成未发生的案例。",
        "读者要的是可执行的边界，而不是更快的口号。",
        "延期那天改的是顺序：先核对事实，再谈排期。",
        "标题承诺的价值必须能在正文里被指出来。",
        "反方意见是速度优先，但缺少核对会把错误放大。",
        "插图只服务已经写清的判断，不拿氛围图充数。",
        "微信稿保留层次，头条稿缩短标题，二者主张相同。",
        "未读原书的情节一律不编，只写作者确认过的经历。",
        "数字只用来源里出现过的，其余写成待确认。",
        "结尾回到观点：边界比速度更值得署名。",
        "如果做不到核对，就停在草稿，不要送直发。",
        "账号定位是克制写实，不堆砌空洞口号。",
        "下一篇仍从同一条公开报道的限制条件起步。",
    ]
    parts.extend(extras)
    parts.extend(f"补充{index}：{line}" for index, line in enumerate(extras))
    parts.append("最后再核对一次公开报道的限制条件，确认正文里每一处判断都站得住，并且篇幅足够支撑一篇可审阅长文。")
    if critique:
        parts.append(f"修订说明：{critique}")
    return project.title + "·有依据", "\n\n".join(parts)


def _visual_fn(project_id, root):
    from pipeline import visuals
    slots = [
        {"id": "vsl_cover", "purpose": "封面", "paragraph_anchor": None, "direction": "封面", "aspect_ratio": "16:9"},
        {"id": "vsl_one", "purpose": "正文插图一", "paragraph_anchor": "正文", "direction": "插图", "aspect_ratio": "16:9"},
        {"id": "vsl_two", "purpose": "正文插图二", "paragraph_anchor": "正文", "direction": "插图", "aspect_ratio": "16:9"},
    ]
    visuals.save_plan(project_id, bible={"style": "plain"}, slots=slots, projects_root=root)
    for index, slot in enumerate(slots):
        asset_id = f"vas_auto_{index}"
        png = root / project_id / "assets" / f"{asset_id}.png"
        png.parent.mkdir(parents=True, exist_ok=True)
        png.write_bytes(_PNG)
        asset = visuals.record_asset(
            project_id, slot_id=slot["id"], prompt="visual", model="fake", size="16:9",
            cost_usd=0, now=NOW, file_path=f"assets/{asset_id}.png",
            status="candidate", asset_id=asset_id, projects_root=root,
        )
        visuals.select_asset(project_id, asset.id, reason="合适", rating=4, projects_root=root)


def test_prepare_pack_rejects_missing_interview_and_sources(tmp_path):
    root = tmp_path / "projects"
    _project(root, autonomy="pack", project_id="prj_packmiss")
    with pytest.raises(AutoCreateError, match="interview"):
        prepare_pack("prj_packmiss", now=NOW, projects_root=root, draft_fn=_draft_fn)
    _interview("prj_packmiss", root)
    with pytest.raises(AutoCreateError, match="来源"):
        prepare_pack("prj_packmiss", now=NOW, projects_root=root, draft_fn=_draft_fn)


def test_prepare_pack_body_is_not_theme_loop(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    _project(root, autonomy="pack", project_id="prj_packreal")
    _interview("prj_packreal", root)
    _source("prj_packreal", root)
    spy = MagicMock(wraps=delivery_service.safe_publish)
    monkeypatch.setattr(delivery_service, "safe_publish", spy)
    result = prepare_pack(
        "prj_packreal", now=NOW, projects_root=root, draft_fn=_draft_fn,
        visual_fn=lambda pid: _visual_fn(pid, root),
    )
    master = load_master("prj_packreal", projects_root=root)
    assert master is not None
    assert "这是自动内容包生成的主稿候选" not in master.body
    assert master.body.count("想法足够支撑一篇候选主稿") < 2
    assert "我的判断是边界比速度重要" in master.body
    assert result.created_master is True
    assert result.revision_count == 0
    assert result.terminal_status in {"drafting", "ready_for_approval"}
    spy.assert_not_called()


def test_gate_failure_pauses_after_two_revisions(tmp_path):
    root = tmp_path / "projects"
    _project(root, autonomy="pack", project_id="prj_packgate")
    _interview("prj_packgate", root)
    _source("prj_packgate", root)
    drafts = {"n": 0}

    def flaky(project, interview, board, critique=None):
        drafts["n"] += 1
        return _draft_fn(project, interview, board, critique)

    with pytest.raises(AutoCreateError, match="质量"):
        run_auto_create(
            "prj_packgate", now=NOW, projects_root=root,
            draft_fn=flaky, score_fn=lambda title, body: ("fail", 3.0),
        )
    assert drafts["n"] == 3  # 初稿 + 两轮修订
    assert load_master("prj_packgate", projects_root=root) is None


def test_score_rejects_repeated_filler():
    verdict, _score, reasons = score_manuscript(
        "标题", "空洞套话赋能抓手底层逻辑。" * 80,
    )
    assert verdict == "fail"
    assert reasons


def test_book_without_excerpt_is_marked_unread(tmp_path):
    root = tmp_path / "projects"
    _project(root, autonomy="pack", project_id="prj_packbook")
    _interview("prj_packbook", root, book=True)
    _source("prj_packbook", root)
    seen = {}

    def capture(project, interview, board, critique=None):
        seen["unread"] = interview.unread_books
        return _draft_fn(project, interview, board, critique)

    run_auto_create(
        "prj_packbook", now=NOW, projects_root=root, draft_fn=capture,
        visual_fn=lambda pid: _visual_fn(pid, root),
    )
    assert "未读书" in seen["unread"]
