"""Project-scoped short video sidecar. Does not publish to any platform."""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from pipeline import master_documents
from pipeline import projects as project_store
from pipeline import visuals as visual_store


_MANIFEST = "video.json"
_VIDEO_DIR = "video"
_PREVIEW_NAME = "preview.mp4"


class ProjectVideoError(ValueError):
    """Video sidecar is missing or invalid."""


@dataclass(frozen=True)
class VideoShot:
    index: int
    line: str
    file_path: str | None


@dataclass(frozen=True)
class ProjectVideo:
    project_id: str
    title: str
    script: str
    duration_s: int
    aspect: str
    shots: tuple[VideoShot, ...]
    file_path: str | None
    updated_at: str


def _project_dir(project_id: str, projects_root: str | Path) -> Path:
    return Path(projects_root) / project_id


def _manifest_path(project_id: str, projects_root: str | Path) -> Path:
    return _project_dir(project_id, projects_root) / _MANIFEST


def _video_dir(project_id: str, projects_root: str | Path) -> Path:
    return _project_dir(project_id, projects_root) / _VIDEO_DIR


def _safe_rel(rel: str | None) -> str | None:
    if rel is None:
        return None
    if not isinstance(rel, str) or not rel.strip():
        raise ProjectVideoError("invalid video path")
    cleaned = rel.replace("\\", "/").lstrip("/")
    path = Path(cleaned)
    if path.is_absolute() or ".." in path.parts or path.parts[0] != _VIDEO_DIR:
        raise ProjectVideoError("invalid video path")
    if path.suffix.lower() != ".mp4":
        raise ProjectVideoError("invalid video path")
    return cleaned


def public_file_url(project_id: str, rel: str | None) -> str | None:
    safe = _safe_rel(rel) if rel else None
    if not safe:
        return None
    return f"/output/projects/{project_id}/{safe}"


def to_payload(pack: ProjectVideo) -> dict[str, Any]:
    return {
        "project_id": pack.project_id,
        "title": pack.title,
        "script": pack.script,
        "duration_s": pack.duration_s,
        "aspect": pack.aspect,
        "shots": [
            {
                "index": shot.index,
                "line": shot.line,
                "file_path": shot.file_path,
                "file_url": public_file_url(pack.project_id, shot.file_path),
            }
            for shot in pack.shots
        ],
        "file_path": pack.file_path,
        "file_url": public_file_url(pack.project_id, pack.file_path),
        "updated_at": pack.updated_at,
        "published": False,
        "destination": None,
    }


def load_video(project_id: str, *, projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT) -> ProjectVideo | None:
    project_store.load_project(project_id, projects_root=projects_root)
    path = _manifest_path(project_id, projects_root)
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    shots = tuple(
        VideoShot(int(item["index"]), str(item["line"]), _safe_rel(item.get("file_path")))
        for item in payload.get("shots") or []
    )
    return ProjectVideo(
        project_id=project_id,
        title=str(payload.get("title") or ""),
        script=str(payload.get("script") or ""),
        duration_s=int(payload.get("duration_s") or 0),
        aspect=str(payload.get("aspect") or "16:9"),
        shots=shots,
        file_path=_safe_rel(payload.get("file_path")),
        updated_at=str(payload.get("updated_at") or ""),
    )


def save_video(
    project_id: str,
    *,
    title: str,
    script: str,
    duration_s: int,
    aspect: str,
    shots: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    file_path: str | None,
    now: str,
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
) -> ProjectVideo:
    project_store.load_project(project_id, projects_root=projects_root)
    parsed = tuple(
        VideoShot(int(item["index"]), str(item["line"]).strip(), _safe_rel(item.get("file_path")))
        for item in shots
    )
    pack = ProjectVideo(
        project_id=project_id,
        title=title.strip(),
        script=script.strip(),
        duration_s=int(duration_s),
        aspect=aspect,
        shots=parsed,
        file_path=_safe_rel(file_path),
        updated_at=now,
    )
    path = _manifest_path(project_id, projects_root)
    path.write_text(json.dumps(asdict(pack), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return pack


def require_master(project_id: str, *, projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT):
    master = master_documents.load_master(project_id, projects_root=projects_root)
    if master is None or not master.body.strip():
        raise ProjectVideoError("先写完文章，再生成视频口播")
    return master


def parse_script_payload(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:]).strip()
    payload = json.loads(cleaned)
    if not isinstance(payload, dict):
        raise ValueError("script must be an object")
    script = payload.get("script")
    shots = payload.get("shots")
    if not isinstance(script, str) or not script.strip():
        raise ValueError("script must be non-empty text")
    if not isinstance(shots, list) or not 3 <= len(shots) <= 6:
        raise ValueError("need 3 to 6 shots")
    parsed = []
    for index, item in enumerate(shots, start=1):
        if not isinstance(item, dict) or not isinstance(item.get("line"), str) or not item["line"].strip():
            raise ValueError("each shot needs a line")
        parsed.append({"index": index, "line": item["line"].strip(), "file_path": item.get("file_path")})
    return {"script": script.strip(), "shots": parsed, "duration_s": int(payload.get("duration_s") or 18)}


def script_prompt(title: str, body: str) -> str:
    excerpt = body.strip()
    if len(excerpt) > 3500:
        excerpt = excerpt[:3500].rstrip() + "…"
    return f"""你是短视频口播编剧。根据这篇已完成的中文长文，写一条 18—30 秒、适合视频号/抖音横版或竖版口播的稿。

标题：{title}

正文：
{excerpt}

规则：
1. 只使用正文里有的判断，不编造数字、人名、公司新闻。
2. 口播分 3 句，对应 3 个镜头。每句 12—28 个字，能念、有停顿。
3. 第一句是钩子，第二句是判断，第三句是收束。不要鸡汤，不要「点赞关注」。
4. 只返回 JSON：{{"script":"三句连在一起的口播","duration_s":18,"shots":[{{"line":"..."}},{{"line":"..."}},{{"line":"..."}}]}}
"""


def fallback_script(title: str, body: str) -> dict[str, Any]:
    paragraphs: list[str] = []
    for raw in body.splitlines():
        text = raw.strip()
        if not text or text.startswith("#") or text.startswith("!"):
            continue
        paragraphs.append(text.rstrip("。！？") + "。")
    if title.strip():
        hook = title.strip().rstrip("。！？") + "。"
    else:
        hook = "这篇刚写完的文章，值得先听三句。"
    while len(paragraphs) < 2:
        paragraphs.append(hook)
    first = _clip_line(hook)
    second = _clip_line(paragraphs[0])
    third = _clip_line(paragraphs[1] if paragraphs[1] != paragraphs[0] else paragraphs[-1])
    shots = [
        {"index": 1, "line": first, "file_path": None},
        {"index": 2, "line": second, "file_path": None},
        {"index": 3, "line": third, "file_path": None},
    ]
    return {"script": "".join(item["line"] for item in shots), "shots": shots, "duration_s": 18}


def _clip_line(text: str) -> str:
    cleaned = "".join(text.split())
    if len(cleaned) <= 28:
        return cleaned if cleaned.endswith(("。", "！", "？")) else cleaned + "。"
    return cleaned[:27].rstrip("，、；：") + "。"


def generate_script(
    title: str,
    body: str,
    *,
    complete_fn: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    prompt = script_prompt(title, body)
    if complete_fn is not None:
        return parse_script_payload(complete_fn(prompt))
    try:
        from pipeline.creators import llm

        return llm.complete_json(
            prompt,
            stage="project_video_script",
            parse=parse_script_payload,
            model_tier="creative",
            max_tokens=1024,
        )
    except Exception:
        return fallback_script(title, body)


def selected_still_paths(
    project_id: str,
    *,
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
) -> list[Path]:
    plan = visual_store.load_visuals(project_id, projects_root=projects_root)
    root = _project_dir(project_id, projects_root)
    paths: list[Path] = []
    for asset in plan.assets:
        if asset.status != "selected" or not asset.file_path:
            continue
        candidate = root / asset.file_path
        if candidate.is_file():
            paths.append(candidate)
    return paths


def _run_ffmpeg(cmd: list[str], *, runner: Callable[..., Any] | None = None) -> None:
    run = runner or subprocess.run
    try:
        run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        tail = (exc.stderr or str(exc))[-400:]
        raise ProjectVideoError(f"ffmpeg failed: {tail}") from exc


def stills_to_clips(
    images: list[Path],
    dest_dir: Path,
    *,
    seconds: int = 6,
    runner: Callable[..., Any] | None = None,
) -> list[Path]:
    if not images:
        raise ProjectVideoError("先给文章配图，再生成视频")
    dest_dir.mkdir(parents=True, exist_ok=True)
    clips: list[Path] = []
    for index, image in enumerate(images[:6], start=1):
        dest = dest_dir / f"shot{index}.mp4"
        _run_ffmpeg(
            [
                "ffmpeg", "-y", "-loop", "1", "-i", str(image),
                "-t", str(seconds),
                "-vf", "scale=854:480:force_original_aspect_ratio=decrease,pad=854:480:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
                "-r", "24", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(dest),
            ],
            runner=runner,
        )
        clips.append(dest)
    return clips


def stitch_clips(
    clip_paths: list[Path],
    dest: Path,
    *,
    runner: Callable[..., Any] | None = None,
) -> Path:
    if not clip_paths:
        raise ProjectVideoError("没有可拼接的镜头")
    dest.parent.mkdir(parents=True, exist_ok=True)
    listing = dest.parent / "concat.txt"
    lines = []
    for clip in clip_paths:
        escaped = str(clip.resolve()).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _run_ffmpeg(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(dest),
        ],
        runner=runner,
    )
    return dest


def import_clips(sources: list[Path], dest_dir: Path) -> list[Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for index, source in enumerate(sources, start=1):
        dest = dest_dir / f"shot{index}.mp4"
        shutil.copy2(source, dest)
        copied.append(dest)
    return copied


def existing_shot_clips(dest_dir: Path) -> list[Path]:
    return sorted(path for path in dest_dir.glob("shot*.mp4") if path.is_file())


def generate_project_video(
    project_id: str,
    *,
    now: str,
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
    complete_fn: Callable[[str], str] | None = None,
    extra_clips: list[Path] | None = None,
    stitch_fn: Callable[..., Path] | None = None,
    stills_fn: Callable[..., list[Path]] | None = None,
) -> ProjectVideo:
    master = require_master(project_id, projects_root=projects_root)
    payload = generate_script(master.title, master.body, complete_fn=complete_fn)
    dest_dir = _video_dir(project_id, projects_root)
    dest_dir.mkdir(parents=True, exist_ok=True)

    if extra_clips:
        clip_paths = import_clips(extra_clips, dest_dir)
    else:
        clip_paths = existing_shot_clips(dest_dir)
        if not clip_paths:
            stills = selected_still_paths(project_id, projects_root=projects_root)
            maker = stills_fn or stills_to_clips
            clip_paths = maker(stills, dest_dir)

    preview = dest_dir / _PREVIEW_NAME
    stitcher = stitch_fn or stitch_clips
    stitcher(clip_paths, preview)

    shot_rows: list[dict[str, Any]] = []
    script_shots = payload["shots"]
    for index, clip in enumerate(clip_paths, start=1):
        line = script_shots[index - 1]["line"] if index <= len(script_shots) else script_shots[-1]["line"]
        shot_rows.append({"index": index, "line": line, "file_path": f"{_VIDEO_DIR}/{clip.name}"})
    if not shot_rows:
        shot_rows = script_shots

    return save_video(
        project_id,
        title=master.title,
        script=payload["script"],
        duration_s=int(payload.get("duration_s") or 6 * max(len(clip_paths), 1)),
        aspect="16:9",
        shots=shot_rows,
        file_path=f"{_VIDEO_DIR}/{_PREVIEW_NAME}",
        now=now,
        projects_root=projects_root,
    )
