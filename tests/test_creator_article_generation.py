from __future__ import annotations

from pathlib import Path

from pipeline import creator_article_generation as generation
from pipeline import master_documents, projects, visuals


def _project(root: Path) -> None:
    projects.create_project(
        title="AI 与普通人的工作", idea="我想写 AI 如何帮助普通人重建工作节奏。",
        audience="普通工作者", goal="完成一篇文章", voice="真实", autonomy="collaborate",
        now="2026-08-12T00:00:00+00:00", project_id="prj_article", projects_root=root,
    )


def test_generation_writes_editable_article_and_embeds_three_contextual_images(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    _project(root)

    outcome = generation.generate_article(
        "prj_article", projects_root=root, now="2026-08-12T00:00:00+00:00",
        write_article=lambda _prompt: {"title": "AI 不是待办清单", "body": "## 从一个真实麻烦开始\n\n先把今天的问题讲清楚。\n\n## 让工具退后一步\n\n再决定工具该做什么。"},
        make_image=lambda _prompt, _ratio: b"png-bytes",
        image_model="test-image", image_cost=lambda _ratio: 0.01,
    )

    assert outcome.status == "completed"
    master = master_documents.load_master("prj_article", projects_root=root)
    assert master is not None
    assert "[IMAGE:" not in master.body
    assert master.body.count("![") == 3
    plan = visuals.load_visuals("prj_article", projects_root=root)
    assert len(plan.slots) == 3
    assert all(asset.status == "selected" for asset in plan.assets)
    assert all((root / "prj_article" / asset.file_path).read_bytes() == b"png-bytes" for asset in plan.assets if asset.file_path)


def test_text_is_preserved_when_one_image_fails_and_retry_is_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    _project(root)
    attempts = {"n": 0}
    def image(_prompt: str, _ratio: str) -> bytes:
        attempts["n"] += 1
        if attempts["n"] == 2:
            raise TimeoutError("image timed out")
        return b"png"

    first = generation.generate_article(
        "prj_article", projects_root=root, now="2026-08-12T00:00:00+00:00",
        write_article=lambda _prompt: {"title": "标题", "body": "## 一\n\n正文。\n\n## 二\n\n结尾。"},
        make_image=image, image_model="test-image", image_cost=lambda _ratio: 0.01,
    )
    assert first.status == "completed_with_errors"
    original = master_documents.load_master("prj_article", projects_root=root)
    assert original is not None and "正文。" in original.body
    assert len([item for item in visuals.load_visuals("prj_article", projects_root=root).assets if item.status == "failed"]) == 1

    repeated = generation.generate_article(
        "prj_article", projects_root=root, now="2026-08-12T00:01:00+00:00",
        write_article=lambda _prompt: (_ for _ in ()).throw(AssertionError("must not regenerate text")),
        make_image=lambda _prompt, _ratio: b"new", image_model="test-image", image_cost=lambda _ratio: 0.01,
    )
    assert repeated.status == "completed_with_errors"
    assert master_documents.load_master("prj_article", projects_root=root).body == original.body
    retried = generation.retry_failed_images("prj_article", projects_root=root, now="2026-08-12T00:02:00+00:00",
        make_image=lambda _prompt, _ratio: b"retry", image_model="test-image", image_cost=lambda _ratio: 0.01)
    assert retried.status == "completed"
    assert master_documents.load_master("prj_article", projects_root=root).body.count("![") == 3


def test_existing_manual_article_is_never_overwritten(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    _project(root)
    master_documents.save_manual("prj_article", title="我的正文", body="不要覆盖", now="2026-08-12T00:00:00+00:00", projects_root=root)

    outcome = generation.generate_article(
        "prj_article", projects_root=root, now="2026-08-12T00:01:00+00:00",
        write_article=lambda _prompt: {"title": "AI", "body": "正文"},
        make_image=lambda _prompt, _ratio: b"png", image_model="test", image_cost=lambda _ratio: 0.01,
    )
    assert outcome.status == "manual_article_exists"
    assert master_documents.load_master("prj_article", projects_root=root).body == "不要覆盖"


def test_text_failure_is_visible_and_a_later_explicit_retry_can_succeed(tmp_path: Path) -> None:
    root = tmp_path / "projects"; _project(root)
    try:
        generation.generate_article("prj_article", projects_root=root, now="2026-08-12T00:00:00+00:00",
            write_article=lambda _prompt: (_ for _ in ()).throw(TimeoutError("text timed out")),
            make_image=lambda _prompt, _ratio: b"png", image_model="test", image_cost=lambda _ratio: 0.0)
    except TimeoutError:
        pass
    assert generation.load_generation("prj_article", projects_root=root).status == "failed_text"
    retry = generation.generate_article("prj_article", projects_root=root, now="2026-08-12T00:01:00+00:00",
        write_article=lambda prompt: {"title": "恢复", "body": "## 一\n\n正文\n\n## 二\n\n结尾"},
        make_image=lambda _prompt, _ratio: b"png", image_model="test", image_cost=lambda _ratio: 0.0,
        source_context=({"citation": "mat_1:1", "text": "已核查事实"},))
    assert retry.status == "completed"
    assert "[mat_1:1]" in generation._article_prompt("作者观点", ({"citation": "mat_1:1", "text": "已核查事实"},))
