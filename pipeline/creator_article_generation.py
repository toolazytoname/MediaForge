"""One explicit creator action that produces an editable illustrated article.

This module deliberately stores progress beside the existing Project v0
sidecar.  It does not add a database table or change the pipeline state
machine: generation is a creator-workspace concern, not a publishing stage.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pipeline import master_documents, projects, visuals
from pipeline.creators import image_gen
from pipeline.utils.ids import new_id


class ArticleGenerationError(ValueError):
    """The provider returned output which cannot become a safe article."""


@dataclass(frozen=True)
class GenerationOutcome:
    status: str
    title: str | None
    body: str | None
    completed_images: int
    failed_images: int
    error: str | None


def generate_article(
    project_id: str,
    *,
    projects_root: str | Path,
    now: str,
    write_article: Callable[[str], dict[str, str]],
    make_image: Callable[[str, str], bytes],
    image_model: str,
    image_cost: Callable[[str], float],
    source_context: tuple[dict[str, str], ...] = (),
) -> GenerationOutcome:
    """Generate once and persist every successful portion before moving on.

    A later repeated click returns the saved outcome.  It therefore never
    bills or replaces a draft twice; failed image entries remain auditable and
    can be retried later through an explicit image action.
    """
    project = projects.load_project(project_id, projects_root=projects_root)
    existing = master_documents.load_master(project_id, projects_root=projects_root)
    saved = _load_state(project_id, projects_root)
    if saved is not None and saved["status"] != "failed_text":
        return _outcome(saved)
    if existing is not None:
        return GenerationOutcome("manual_article_exists", existing.title, existing.body, 0, 0, None)

    _save_state(project_id, {"status": "drafting", "title": None, "body": None, "completed_images": 0, "failed_images": 0, "error": None}, projects_root)
    try:
        article = write_article(_article_prompt(project.idea, source_context))
    except Exception as error:
        state = {"status": "failed_text", "title": None, "body": None, "completed_images": 0,
                 "failed_images": 0, "error": _safe_error(error)}
        _save_state(project_id, state, projects_root)
        raise
    title, body = _article(article)
    master = master_documents.save_manual(project_id, title=title, body=body, now=now, projects_root=projects_root)
    _save_state(project_id, {"status": "preparing_images", "title": title, "body": body, "completed_images": 0, "failed_images": 0, "error": None}, projects_root)

    slots = _slots(body)
    visuals.save_plan(project_id, bible={"style": "真实、克制、与正文信息相关，不包含文字或水印"}, slots=slots, projects_root=projects_root)
    embedded: list[tuple[str | None, str, str]] = []
    completed = failed = 0
    for slot in visuals.load_visuals(project_id, projects_root=projects_root).slots:
        prompt = _image_prompt(title, slot)
        asset_id = new_id("vas")
        try:
            data = make_image(prompt, slot.aspect_ratio)
            if not data:
                raise ArticleGenerationError("image provider returned an empty image")
            path = visuals.asset_path(project_id, asset_id, projects_root=projects_root)
            image_gen._write_atomic(path, data)
            asset = visuals.record_asset(project_id, slot_id=slot.id, prompt=prompt, model=image_model,
                size=slot.aspect_ratio, cost_usd=image_cost(slot.aspect_ratio), now=now,
                file_path=f"assets/{asset_id}.png", status="candidate", projects_root=projects_root, asset_id=asset_id)
            visuals.select_asset(project_id, asset.id, reason="首次生成：与文章段落对应", rating=None, projects_root=projects_root)
            embedded.append((slot.paragraph_anchor, f"![{slot.purpose}](/output/projects/{project_id}/{asset.file_path})", slot.purpose))
            completed += 1
        except Exception as error:  # Individual image failures must not discard the article.
            visuals.record_asset(project_id, slot_id=slot.id, prompt=prompt, model=image_model,
                size=slot.aspect_ratio, cost_usd=0.0, now=now, file_path=None, status="failed",
                failure=_safe_error(error), projects_root=projects_root, asset_id=asset_id)
            failed += 1
        _save_state(project_id, {"status": "preparing_images", "title": title, "body": body,
            "completed_images": completed, "failed_images": failed, "error": None}, projects_root)

    rendered = _embed_images(body, embedded)
    # This explicit generation owns only the just-created initial version.  A
    # simultaneous human save wins; never replace it merely to add image URLs.
    current = master_documents.load_master(project_id, projects_root=projects_root)
    if current is not None and current.version == master.version and current.body == body:
        master_documents.save_manual(project_id, title=title, body=rendered, now=now, projects_root=projects_root)
    status = "completed" if failed == 0 else "completed_with_errors"
    state = {"status": status, "title": title, "body": rendered, "completed_images": completed, "failed_images": failed,
             "error": "部分图片未完成，可在文章中单独重试。" if failed else None}
    _save_state(project_id, state, projects_root)
    return _outcome(state)


def load_generation(project_id: str, *, projects_root: str | Path) -> GenerationOutcome | None:
    projects.load_project(project_id, projects_root=projects_root)
    state = _load_state(project_id, projects_root)
    return _outcome(state) if state is not None else None


def retry_failed_images(project_id: str, *, projects_root: str | Path, now: str,
                        make_image: Callable[[str, str], bytes], image_model: str,
                        image_cost: Callable[[str], float]) -> GenerationOutcome:
    """Retry only failed image slots; the article text is never regenerated."""
    current = master_documents.load_master(project_id, projects_root=projects_root)
    if current is None:
        raise ArticleGenerationError("article must exist before retrying images")
    plan = visuals.load_visuals(project_id, projects_root=projects_root)
    selected_slots = {asset.slot_id for asset in plan.assets if asset.status == "selected"}
    newly_embedded: list[tuple[str | None, str, str]] = []
    for slot in plan.slots:
        if slot.id in selected_slots or not any(asset.slot_id == slot.id and asset.status == "failed" for asset in plan.assets):
            continue
        prompt = _image_prompt(current.title, slot); asset_id = new_id("vas")
        try:
            data = make_image(prompt, slot.aspect_ratio)
            if not data: raise ArticleGenerationError("image provider returned an empty image")
            path = visuals.asset_path(project_id, asset_id, projects_root=projects_root); image_gen._write_atomic(path, data)
            asset = visuals.record_asset(project_id, slot_id=slot.id, prompt=prompt, model=image_model, size=slot.aspect_ratio,
                cost_usd=image_cost(slot.aspect_ratio), now=now, file_path=f"assets/{asset_id}.png", status="candidate", projects_root=projects_root, asset_id=asset_id)
            visuals.select_asset(project_id, asset.id, reason="重试后选择", rating=None, projects_root=projects_root)
            newly_embedded.append((slot.paragraph_anchor, f"![{slot.purpose}](/output/projects/{project_id}/{asset.file_path})", slot.purpose))
        except Exception as error:
            visuals.record_asset(project_id, slot_id=slot.id, prompt=prompt, model=image_model, size=slot.aspect_ratio,
                cost_usd=0.0, now=now, file_path=None, status="failed", failure=_safe_error(error), projects_root=projects_root, asset_id=asset_id)
    if newly_embedded:
        master_documents.save_manual(project_id, title=current.title, body=_embed_images(current.body, newly_embedded), now=now, projects_root=projects_root)
    refreshed = visuals.load_visuals(project_id, projects_root=projects_root)
    completed = len({asset.slot_id for asset in refreshed.assets if asset.status == "selected"})
    failed = len([slot for slot in refreshed.slots if slot.id not in {asset.slot_id for asset in refreshed.assets if asset.status == "selected"} and any(asset.slot_id == slot.id and asset.status == "failed" for asset in refreshed.assets)])
    state = {"status": "completed" if failed == 0 else "completed_with_errors", "title": current.title,
             "body": master_documents.load_master(project_id, projects_root=projects_root).body, "completed_images": completed,
             "failed_images": failed, "error": "部分图片未完成，可在文章中单独重试。" if failed else None}
    _save_state(project_id, state, projects_root)
    return _outcome(state)


def _article_prompt(idea: str, sources: tuple[dict[str, str], ...]) -> str:
    source_text = "\n".join(f"- [{item['citation']}] {item['text']}" for item in sources) or "（没有已核查外部资料）"
    return f"""你是中文非虚构编辑。把作者的想法写成一篇可直接继续编辑的 Markdown 文章。
作者想法：{idea}
已核查资料（可作为事实，首次使用时保留方括号引用编号）：
{source_text}
规则：标题明确；使用至少两个 ## 小节；作者想法属于作者判断，不能伪装成外部事实；没有在资料里的信息只能明确写成推断或不写；不编造数据、引语、经历或来源；文章完整可读；不要输出图片占位符、[IMAGE]、HTML 或代码围栏。
只返回 JSON：{{\"title\":\"...\",\"body\":\"...\"}}。"""


def _article(value: object) -> tuple[str, str]:
    if not isinstance(value, dict) or set(value) != {"title", "body"}:
        raise ArticleGenerationError("article provider must return title and body only")
    title, body = value["title"], value["body"]
    if not isinstance(title, str) or not title.strip() or not isinstance(body, str) or not body.strip():
        raise ArticleGenerationError("article title and body must be non-empty text")
    if "[IMAGE" in body.upper():
        raise ArticleGenerationError("article must not contain image placeholders")
    return title.strip(), body.strip()


def _slots(body: str) -> list[dict[str, object]]:
    headings = [line[3:].strip() for line in body.splitlines() if line.startswith("## ")]
    anchors = (headings + [None, None])[:2]
    return [
        {"id": new_id("vsl"), "purpose": "文章封面：呈现核心问题", "paragraph_anchor": None, "direction": "横向、有留白的编辑感画面", "aspect_ratio": "16:9"},
        {"id": new_id("vsl"), "purpose": "插图一：帮助理解第一段论述", "paragraph_anchor": anchors[0], "direction": "与首段观点相关的真实生活场景", "aspect_ratio": "4:3"},
        {"id": new_id("vsl"), "purpose": "插图二：帮助理解后文转折", "paragraph_anchor": anchors[1], "direction": "与后文观点相关的克制插画", "aspect_ratio": "4:3"},
    ]


def _image_prompt(title: str, slot: visuals.VisualSlot) -> str:
    return f"为中文文章《{title}》生成{slot.purpose}。画面方向：{slot.direction}。不含文字、logo、水印，不描绘虚构的具体事实。"


def _embed_images(body: str, images: list[tuple[str | None, str, str]]) -> str:
    cover = next((markdown for anchor, markdown, _alt in images if anchor is None), None)
    result = f"{cover}\n\n{body}" if cover else body
    for anchor, markdown, _alt in images:
        if anchor is None:
            continue
        heading = f"## {anchor}"
        if heading in result:
            result = result.replace(heading, f"{heading}\n\n{markdown}", 1)
    return result


def _state_path(project_id: str, root: str | Path) -> Path:
    return Path(root) / project_id / "article_generation.json"


def _load_state(project_id: str, root: str | Path) -> dict[str, object] | None:
    path = _state_path(project_id, root)
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ArticleGenerationError("invalid article generation state") from error
    required = {"status", "title", "body", "completed_images", "failed_images", "error"}
    if not isinstance(state, dict) or set(state) != required:
        raise ArticleGenerationError("invalid article generation state")
    allowed = {"drafting", "failed_text", "preparing_images", "completed", "completed_with_errors"}
    if state["status"] not in allowed or not isinstance(state["completed_images"], int) or isinstance(state["completed_images"], bool) or state["completed_images"] < 0 or not isinstance(state["failed_images"], int) or isinstance(state["failed_images"], bool) or state["failed_images"] < 0:
        raise ArticleGenerationError("invalid article generation state")
    if state["title"] is not None and not isinstance(state["title"], str) or state["body"] is not None and not isinstance(state["body"], str) or state["error"] is not None and not isinstance(state["error"], str):
        raise ArticleGenerationError("invalid article generation state")
    return state


def _save_state(project_id: str, state: dict[str, object], root: str | Path) -> None:
    path = _state_path(project_id, root)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def _outcome(state: dict[str, object]) -> GenerationOutcome:
    return GenerationOutcome(str(state["status"]), state["title"] if isinstance(state["title"], str) else None,
        state["body"] if isinstance(state["body"], str) else None, int(state["completed_images"]), int(state["failed_images"]),
        state["error"] if isinstance(state["error"], str) else None)


def _safe_error(error: Exception) -> str:
    return f"{type(error).__name__}: {str(error)[:240]}"
