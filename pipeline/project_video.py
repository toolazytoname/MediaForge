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
    if not isinstance(shots, list) or not 3 <= len(shots) <= 8:
        raise ValueError("need 3 to 8 shots")
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
    return f"""你是短视频口播编剧。按剪映/口播短视频的成熟流程写稿：先能念，再能切镜头。

标题：{title}

正文：
{excerpt}

规则：
1. 只使用正文里有的判断，不编造数字、人名、公司新闻。
2. 写成 45—75 秒口播，大约 180—280 个汉字，4—6 句。每句 16—40 个字，能一口气念完。
3. 结构固定：钩子 → 判断 → 具体例子 → 收束。不要鸡汤，不要「点赞关注」「本文」。
4. 每句对应一个镜头字幕。script 是连起来能直接配音的全文。
5. 只返回 JSON：{{"script":"完整口播","duration_s":55,"shots":[{{"line":"..."}},{{"line":"..."}},{{"line":"..."}},{{"line":"..."}}]}}
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
    while len(paragraphs) < 3:
        paragraphs.append(hook)
    picks = [hook, paragraphs[0], paragraphs[1], paragraphs[2] if len(paragraphs) > 2 else paragraphs[-1]]
    shots = [{"index": index, "line": _clip_line(item), "file_path": None} for index, item in enumerate(picks[:5], start=1)]
    return {"script": "".join(item["line"] for item in shots), "shots": shots, "duration_s": 55}


def _clip_line(text: str) -> str:
    cleaned = "".join(text.split())
    if len(cleaned) <= 40:
        return cleaned if cleaned.endswith(("。", "！", "？")) else cleaned + "。"
    return cleaned[:39].rstrip("，、；：") + "。"


def generate_script(
    title: str,
    body: str,
    *,
    complete_fn: Callable[[str], str] | None = None,
    allow_llm: bool = True,
) -> dict[str, Any]:
    prompt = script_prompt(title, body)
    if complete_fn is not None:
        return parse_script_payload(complete_fn(prompt))
    if not allow_llm:
        return fallback_script(title, body)
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


def _run_ffmpeg(cmd: list[str], *, runner: Callable[..., Any] | None = None, cwd: Path | None = None) -> None:
    run = runner or subprocess.run
    try:
        run(cmd, check=True, capture_output=True, text=True, cwd=str(cwd) if cwd else None)
    except subprocess.CalledProcessError as exc:
        tail = (exc.stderr or str(exc))[-400:]
        raise ProjectVideoError(f"ffmpeg failed: {tail}") from exc


def chinese_font() -> Path | None:
    for candidate in (
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/System/Library/Fonts/STHeiti Light.ttc"),
        Path("/System/Library/Fonts/Supplemental/Songti.ttc"),
        Path("/Library/Fonts/Arial Unicode.ttf"),
    ):
        if candidate.is_file():
            return candidate
    return None


def probe_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return max(float(result.stdout.strip() or "0"), 1.0)


def allocate_shot_times(lines: list[str], total: float) -> list[float]:
    weights = [max(len(line), 8) for line in lines]
    scale = total / sum(weights)
    times = [round(weight * scale, 2) for weight in weights]
    drift = total - sum(times)
    times[-1] = round(times[-1] + drift, 2)
    return [max(item, 1.6) for item in times]


def write_srt(lines: list[str], times: list[float], dest: Path) -> Path:
    def stamp(seconds: float) -> str:
        whole = max(int(seconds), 0)
        millis = int(round((seconds - whole) * 1000))
        hours, rem = divmod(whole, 3600)
        minutes, secs = divmod(rem, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    cursor = 0.0
    blocks: list[str] = []
    for index, (line, length) in enumerate(zip(lines, times), start=1):
        start, cursor = cursor, cursor + length
        blocks.append(f"{index}\n{stamp(start)} --> {stamp(cursor)}\n{line}\n")
    dest.write_text("\n".join(blocks) + "\n", encoding="utf-8")
    return dest


def render_caption_card(text: str, dest: Path, *, width: int = 1000) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    def wrap(raw: str) -> str:
        cleaned = raw.strip()
        if len(cleaned) <= 16:
            return cleaned
        for sep in ("，", "。", "；", "、", "："):
            idx = cleaned.rfind(sep, 0, 18)
            if idx >= 6:
                return cleaned[: idx + 1] + "\n" + cleaned[idx + 1 :]
        return cleaned[:16] + "\n" + cleaned[16:]

    body = wrap(text)
    font_path = chinese_font()
    font = ImageFont.truetype(str(font_path), 40, index=0) if font_path else ImageFont.load_default()
    probe = Image.new("RGBA", (width, 200), (0, 0, 0, 0))
    drawer = ImageDraw.Draw(probe)
    box = drawer.multiline_textbbox((0, 0), body, font=font, align="center", spacing=8)
    text_w, text_h = box[2] - box[0], box[3] - box[1]
    pad_x, pad_y = 28, 18
    card_w = int(min(width, text_w + pad_x * 2))
    card_h = int(text_h + pad_y * 2)
    img = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, card_w - 1, card_h - 1), radius=18, fill=(12, 12, 12, 180))
    draw.multiline_text(((card_w - text_w) / 2, pad_y - box[1]), body, font=font, fill=(255, 255, 255, 255), align="center", spacing=8)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest)
    return dest


def mix_voice_and_captions(
    video: Path,
    audio: Path,
    srt: Path,
    dest: Path,
    *,
    runner: Callable[..., Any] | None = None,
) -> Path:
    tmp = dest.with_name(dest.stem + ".mix.mp4")
    workdir = dest.parent
    cmd = [
        "ffmpeg", "-y", "-i", video.name, "-i", audio.name,
        "-filter_complex", "[0:v]tpad=stop=-1[v]",
        "-map", "[v]", "-map", "1:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", tmp.name,
    ]
    _run_ffmpeg(cmd, runner=runner, cwd=workdir)
    tmp.replace(dest)
    return dest


def stills_to_clips(
    images: list[Path],
    dest_dir: Path,
    *,
    seconds: float = 6,
    durations: list[float] | None = None,
    lines: list[str] | None = None,
    runner: Callable[..., Any] | None = None,
) -> list[Path]:
    if not images:
        raise ProjectVideoError("先给文章配图，再生成视频")
    dest_dir.mkdir(parents=True, exist_ok=True)
    clips: list[Path] = []
    count = len(durations) if durations else min(len(images), 6)
    count = max(count, 1)
    for index in range(1, count + 1):
        image = images[(index - 1) % len(images)]
        dest = dest_dir / f"shot{index}.mp4"
        hold = durations[index - 1] if durations and index <= len(durations) else seconds
        fade = 0.35 if hold > 1.2 else 0.0
        fade_tail = f",fade=t=in:st=0:d={fade},fade=t=out:st={max(hold - fade, 0):.2f}:d={fade}" if fade else ""
        inputs = ["-y", "-loop", "1", "-i", str(image)]
        if lines and index <= len(lines):
            card = dest_dir / f"caption{index}.png"
            render_caption_card(lines[index - 1], card)
            inputs.extend(["-loop", "1", "-i", str(card)])
            vf = (
                "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920[bg];"
                f"[bg][1:v]overlay=(W-w)/2:H-h-110:format=auto{fade_tail},format=yuv420p[v]"
            )
        else:
            vf = (
                f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
                f"{fade_tail},format=yuv420p[v]"
            )
        _run_ffmpeg(
            [
                "ffmpeg", *inputs,
                "-t", f"{hold:.2f}",
                "-filter_complex", vf,
                "-map", "[v]",
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
    tts_fn: Callable[[str, Path], Path] | None = None,
    mix_fn: Callable[..., Path] | None = None,
    allow_llm: bool = True,
) -> ProjectVideo:
    master = require_master(project_id, projects_root=projects_root)
    payload = generate_script(master.title, master.body, complete_fn=complete_fn, allow_llm=allow_llm)
    dest_dir = _video_dir(project_id, projects_root)
    dest_dir.mkdir(parents=True, exist_ok=True)

    voice = dest_dir / "voice.mp3"
    if tts_fn is not None:
        tts_fn(payload["script"], voice)
    else:
        try:
            from pipeline.creators.tts import synthesize_speech

            synthesize_speech(payload["script"], voice)
        except Exception as exc:
            raise ProjectVideoError(f"口播配音失败：{exc}") from exc

    try:
        spoken = probe_duration(voice)
    except Exception:
        spoken = float(payload.get("duration_s") or 55)
    lines = [item["line"] for item in payload["shots"]]
    times = allocate_shot_times(lines, spoken)

    if extra_clips:
        clip_paths = import_clips(extra_clips, dest_dir)
    else:
        stills = selected_still_paths(project_id, projects_root=projects_root)
        if not stills:
            clip_paths = existing_shot_clips(dest_dir)
            if not clip_paths:
                raise ProjectVideoError("先给文章配图，再生成视频")
        else:
            maker = stills_fn or stills_to_clips
            clip_paths = maker(stills, dest_dir, durations=times, lines=lines)

    silent = dest_dir / "silent.mp4"
    preview = dest_dir / _PREVIEW_NAME
    stitcher = stitch_fn or stitch_clips
    stitcher(clip_paths, silent)

    srt = write_srt(lines[:len(clip_paths)] or lines, times[:len(clip_paths)] or times, dest_dir / "captions.srt")
    mixer = mix_fn or mix_voice_and_captions
    mixer(silent, voice, srt, preview)

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
        duration_s=int(round(spoken)),
        aspect="9:16",
        shots=shot_rows,
        file_path=f"{_VIDEO_DIR}/{_PREVIEW_NAME}",
        now=now,
        projects_root=projects_root,
    )
