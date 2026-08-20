from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from PIL import Image, ImageOps

from .ai_director import AIDirector
from .alignment import SpeechAlignmentService
from .comfyui import ComfyUIClient
from .config import settings
from .event_stream import EventBroker
from .orchestrator import TaskRunner
from .production_queue import ProductionQueue
from .repository import ProjectRepository
from .schemas import (
    AppSettingsPatch,
    ComfyCheckRequest,
    ProjectCreate,
    ProjectPatch,
    TaskPatch,
    TTS_ENGINES,
)
from .workflows import REQUIRED_VIDEO_NODES, WorkflowCompiler, required_tts_nodes


@asynccontextmanager
async def lifespan(_: FastAPI):
    event_broker.bind_loop(asyncio.get_running_loop())
    await production_queue.start()
    runner.resume_interrupted_videos()
    try:
        yield
    finally:
        await production_queue.stop()


app = FastAPI(title="数字人工厂", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.frontend_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
repository = ProjectRepository(settings.data_dir / "jobs.db")
event_broker = EventBroker()
repository.add_listener(event_broker.publish)
director = AIDirector(settings)
aligner = SpeechAlignmentService(settings)
compiler = WorkflowCompiler(settings.root)
runner = TaskRunner(settings, repository, director, aligner, compiler)
production_queue = ProductionQueue(repository, runner)


def global_comfy_url() -> str:
    return repository.get_setting("comfy_url", settings.default_comfy_url)


PROJECT_DISPLAY_PROGRESS = {
    "CREATED": 0,
    "QUEUE_WAITING": 0,
    "UPLOADING_ASSETS": 1,
    "ANALYZE_QUEUED": 2,
    "ANALYZING_IMAGE": 5,
    "ANALYSIS_RETRYING": 5,
    "SCRIPT_READY": 20,
    "AUDIO_QUEUED": 20,
    "UPLOADING_REFERENCE_AUDIO": 22,
    "GENERATING_AUDIO": 28,
    "ALIGN_QUEUED": 34,
    "ALIGNING_SPEECH": 36,
    "PLANNING_ACTIONS": 38,
    "PLAN_READY": 40,
    "VIDEO_QUEUED": 40,
    "UPLOADING_VIDEO_ASSETS": 42,
    "GENERATING_VIDEO": 50,
    "VIDEO_READY": 88,
    "SUBTITLE_QUEUED": 88,
    "BURNING_SUBTITLES": 92,
    "SUBTITLE_READY": 96,
    "BGM_QUEUED": 96,
    "MIXING_BGM": 98,
    "BGM_ERROR": 100,
    "COMPLETED": 100,
    "QUEUE_CANCELLED": 0,
}


def project_display_progress(project: dict[str, Any]) -> int:
    """Convert per-stage runner progress into one monotonic production percentage."""
    status = str(project.get("status") or "")
    if status == "GENERATING_VIDEO":
        total = max(0, int(project.get("video_segment_total") or 0))
        current = max(0, int(project.get("video_segment_current") or 0))
        local = min(1.0, max(0.0, float(project.get("video_segment_progress") or 0)))
        if total:
            fraction = min(1.0, (max(0, current - 1) + local) / total)
            return min(87, 40 + round(fraction * 47))
    if status in PROJECT_DISPLAY_PROGRESS:
        return PROJECT_DISPLAY_PROGRESS[status]
    raw = max(0, min(100, int(project.get("progress") or 0)))
    if status == "ERROR":
        if project.get("bgm_video_path"):
            return 100
        if project.get("subtitle_video_path"):
            return max(96, raw)
        if project.get("raw_video_path") or project.get("video_path"):
            return max(88, raw)
        if project.get("audio_path") and project.get("segments"):
            return max(40, raw)
        if project.get("script") and project.get("image_analysis"):
            return max(20, raw)
    return raw


def suggest_video_title(script: str) -> str:
    text = re.sub(r"\s+", "", str(script or "")).strip()
    if not text:
        return ""
    sentences = [part for part in re.split(r"[\u3002\uff01\uff1f!?]+", text) if part]
    first = sentences[0] if sentences else text
    clauses = [part.strip("\uff0c,\uff1b;") for part in re.split(r"[\uff0c,\uff1b;]", first) if part.strip("\uff0c,\uff1b;")]
    lines = clauses[:3]
    if len(lines) == 1 and len(lines[0]) > 12:
        value = lines[0]
        lines = [value[offset : offset + 12] for offset in range(0, min(len(value), 36), 12)]
    if len(lines) == 1 and len(sentences) > 1:
        lines.extend(sentences[1:3])
    return "\n".join(line[:12] for line in lines[:3] if line)


def public_project(project: dict[str, Any]) -> dict[str, Any]:
    hidden = {
        "comfy_url",
        "image_path",
        "voice_path",
        "emotion_voice_path",
        "bgm_path",
        "audio_path",
        "video_path",
        "raw_video_path",
        "bgm_video_path",
        "subtitle_video_path",
        "subtitle_path",
        "subtitle_ass_path",
        "project_dir",
    }
    result = {key: value for key, value in project.items() if key not in hidden}
    result["stage_progress"] = max(0, min(100, int(project.get("progress") or 0)))
    result["progress"] = project_display_progress(project)

    def exists(field: str) -> bool:
        value = project.get(field)
        return bool(value and Path(value).exists())

    result["has_image"] = exists("image_path")
    result["has_voice"] = exists("voice_path")
    result["has_emotion_voice"] = exists("emotion_voice_path")
    result["has_audio"] = exists("audio_path")
    result["has_video"] = exists("video_path")
    result["has_bgm"] = exists("bgm_path")
    legacy_raw_video = bool(
        not project.get("bgm_video_path")
        and project.get("video_path")
        and Path(project["video_path"]).exists()
    )
    result["has_raw_video"] = exists("raw_video_path") or legacy_raw_video
    result["has_bgm_video"] = exists("bgm_video_path")
    result["has_subtitle_video"] = exists("subtitle_video_path")
    result["has_subtitle"] = exists("subtitle_path")
    result.setdefault("bgm_enabled", False)
    result.setdefault("bgm_volume", 0.25)
    result.setdefault("bgm_ducking", True)
    result.setdefault("bgm_fade_in", 1.5)
    result.setdefault("bgm_fade_out", 2.0)
    result.setdefault("subtitle_enabled", False)
    result.setdefault("subtitle_font_name", "Microsoft YaHei")
    result.setdefault("subtitle_font_size", 64)
    result.setdefault("subtitle_font_bold", True)
    result.setdefault("subtitle_font_color", "#FFFFFF")
    result.setdefault("subtitle_position", "custom")
    result.setdefault("subtitle_custom_position", 73)
    result.setdefault("subtitle_stroke_color", "#000000")
    result.setdefault("subtitle_stroke_width", 3)
    result.setdefault("subtitle_background_enabled", False)
    result.setdefault("subtitle_background_color", "#000000")
    result.setdefault("subtitle_background_opacity", 40)
    result.setdefault("subtitle_max_chars", 14)
    result.setdefault("video_title_enabled", True)
    if not str(result.get("video_title") or "").strip():
        result["video_title"] = suggest_video_title(project.get("original_script") or project.get("script") or "")
    result.setdefault("video_title_font_name", "Microsoft YaHei")
    result.setdefault("video_title_font_size", 88)
    result.setdefault("video_title_primary_color", "#FFFFFF")
    result.setdefault("video_title_secondary_color", "#FFD84D")
    result.setdefault("video_title_position", 10)
    result.setdefault("video_title_stroke_color", "#000000")
    result.setdefault("video_title_stroke_width", 4)
    result["can_edit_original_script"] = can_edit_project_script(project)
    return result


def task_display_state(
    task: dict[str, Any], project: dict[str, Any] | None
) -> dict[str, Any]:
    task_status = task.get("status", "")
    if task_status == "QUEUED":
        return {"display_status": "QUEUE_WAITING", "status_source": "queue"}
    if task_status == "RUNNING":
        return {
            "display_status": (project or {}).get("status") or task.get("stage", "RUNNING"),
            "status_source": "project" if project else "queue",
        }
    project_advanced = bool(
        project
        and project.get("updated_at", "") > task.get("updated_at", "")
        and project.get("status") not in {"ERROR", "QUEUE_CANCELLED"}
    )
    if project_advanced:
        return {
            "display_status": project.get("status", task_status),
            "status_source": "project_after_terminal_task",
        }
    terminal_status = {
        "FAILED": "ERROR",
        "CANCELLED": "QUEUE_CANCELLED",
        "COMPLETED": "COMPLETED",
    }
    return {
        "display_status": terminal_status.get(task_status, task_status),
        "status_source": "queue",
    }


def public_task(task: dict[str, Any], queue_position: int | None = None) -> dict[str, Any]:
    result = dict(task)
    project = repository.get(task["project_id"])
    result.update(task_display_state(task, project))
    if project:
        result["project_title"] = project.get("title", task.get("project_title", ""))
        result["original_script"] = project.get(
            "original_script", (task.get("snapshot") or {}).get("original_script", "")
        )
        result["project_status"] = project.get("status", "")
        result["project_stage_progress"] = max(
            0, min(100, int(project.get("progress") or 0))
        )
        result["project_progress"] = project_display_progress(project)
        result["display_progress"] = result["project_progress"]
        result["has_audio"] = bool(
            project.get("audio_path") and Path(project["audio_path"]).is_file()
        )
        result["has_video"] = bool(
            project.get("video_path") and Path(project["video_path"]).is_file()
        )
        result["has_image"] = bool(
            project.get("image_path") and Path(project["image_path"]).is_file()
        )
        result["portrait_url"] = f"/api/projects/{task['project_id']}/files/image"
        result["can_edit_script"] = can_edit_task_script(task, project)
        for key in (
            "video_segment_current",
            "video_segment_completed",
            "video_segment_total",
            "video_segment_progress",
            "video_node_value",
            "video_node_max",
            "video_progress_mode",
        ):
            result[key] = project.get(key)
    else:
        result["display_progress"] = task.get("progress", 0)
        result["original_script"] = (task.get("snapshot") or {}).get(
            "original_script", ""
        )
        result["has_image"] = False
        result["can_edit_script"] = False
    result["can_edit_title"] = bool(project)
    result["can_delete"] = True
    if queue_position is not None:
        result["queue_position"] = queue_position
    return result


def public_project_summary(project: dict[str, Any]) -> dict[str, Any]:
    image_path = Path(str(project.get("image_path") or ""))
    has_image = image_path.is_file()
    thumbnail_version = (
        f"{image_path.stat().st_mtime_ns}-{image_path.stat().st_size}"
        if has_image
        else ""
    )
    return {
        "id": project["id"],
        "title": project.get("title", project["id"]),
        "status": project.get("status", "CREATED"),
        "progress": project_display_progress(project),
        "stage_progress": max(0, min(100, int(project.get("progress") or 0))),
        "updated_at": project.get("updated_at"),
        "created_at": project.get("created_at"),
        "audio_duration": project.get("audio_duration"),
        "segment_count": len(project.get("segments") or []),
        "has_image": has_image,
        "thumbnail_version": thumbnail_version,
        "has_audio": bool(project.get("audio_path") and Path(project["audio_path"]).is_file()),
        "has_video": bool(project.get("video_path") and Path(project["video_path"]).is_file()),
        "video_segment_current": project.get("video_segment_current"),
        "video_segment_completed": project.get("video_segment_completed"),
        "video_segment_total": project.get("video_segment_total"),
        "video_segment_progress": project.get("video_segment_progress"),
        "error": project.get("error"),
    }


def public_task_summary(task: dict[str, Any], queue_position: int | None = None) -> dict[str, Any]:
    full = public_task(task, queue_position)
    keys = (
        "id", "project_id", "project_title", "status", "stage", "progress",
        "display_status", "display_progress", "status_source", "queue_position",
        "created_at", "updated_at", "started_at", "completed_at", "error",
        "project_status", "project_progress", "project_stage_progress",
        "video_segment_current", "video_segment_completed", "video_segment_total",
        "video_segment_progress", "video_node_value", "video_node_max",
        "video_progress_mode", "can_delete",
    )
    result = {key: full.get(key) for key in keys if key in full}
    result["tts_engine"] = (task.get("snapshot") or {}).get("tts_engine")
    return result


AUDIO_LOCKED_TASK_STAGES = {
    "GENERATING_AUDIO",
    "GENERATING_VIDEO",
    "COMPLETED",
}
AUDIO_LOCKED_PROJECT_STATUSES = {
    "UPLOADING_REFERENCE_AUDIO",
    "GENERATING_AUDIO",
    "ALIGNING_SPEECH",
    "PLANNING_ACTIONS",
    "PLAN_READY",
    "UPLOADING_VIDEO_ASSETS",
    "GENERATING_VIDEO",
    "COMPLETED",
}


def can_edit_task_script(
    task: dict[str, Any], project: dict[str, Any] | None
) -> bool:
    if not project:
        return False
    if task.get("stage") in AUDIO_LOCKED_TASK_STAGES:
        return False
    return can_edit_project_script(project)


def can_edit_project_script(project: dict[str, Any]) -> bool:
    if project.get("audio_started") or project.get("audio_path"):
        return False
    return project.get("status") not in AUDIO_LOCKED_PROJECT_STATUSES


def production_stage_locked(project: dict[str, Any], stage: str) -> bool:
    if stage not in {"analyze", "audio", "video"}:
        return False
    return any(
        bool(value and Path(value).is_file())
        for value in (project.get("raw_video_path"), project.get("video_path"))
    )


def build_project_thumbnail(
    source: Path,
    destination: Path,
    size: tuple[int, int] = (240, 320),
) -> Path:
    if not source.is_file():
        raise FileNotFoundError(source)
    if destination.is_file() and destination.stat().st_mtime_ns >= source.stat().st_mtime_ns:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.stem}-{uuid.uuid4().hex}.tmp.webp")
    try:
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            thumbnail = ImageOps.fit(image, size, method=Image.Resampling.LANCZOS)
            thumbnail.save(temporary, format="WEBP", quality=68, method=4)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def get_project(project_id: str) -> dict[str, Any]:
    project = repository.get(project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    return project


def safe_project_directory(project: dict[str, Any]) -> Path:
    jobs_root = (settings.data_dir / "jobs").resolve()
    raw_path = Path(str(project.get("project_dir") or ""))
    if not raw_path.is_absolute():
        raise HTTPException(409, "项目目录不是绝对路径，已拒绝删除")
    target = raw_path.resolve()
    if target.parent != jobs_root or target.name != project["id"]:
        raise HTTPException(409, "项目目录超出 data/jobs 安全边界，已拒绝删除")
    is_junction = getattr(os.path, "isjunction", lambda _: False)
    if target.exists():
        pending = [target]
        while pending:
            current = pending.pop()
            if current.is_symlink() or is_junction(current):
                raise HTTPException(409, f"项目目录包含链接或联接点，已拒绝删除：{current}")
            if not current.is_dir():
                continue
            with os.scandir(current) as entries:
                for entry in entries:
                    child = Path(entry.path)
                    if child.is_symlink() or is_junction(child):
                        raise HTTPException(409, f"项目目录包含链接或联接点，已拒绝删除：{child}")
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(child)
    return target


async def save_upload(upload: UploadFile, destination: Path, limit: int) -> None:
    content = await upload.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(413, f"文件超过限制：{limit // 1024 // 1024}MB")
    if not content:
        raise HTTPException(400, "上传文件为空")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)


def create_default_project(payload: ProjectCreate) -> dict[str, Any]:
    if not payload.original_script.strip():
        raise HTTPException(422, "口播文案不能为空")
    image_source = settings.root / "专业肖像照.png"
    voice_source = settings.root / "40049511080-1-30280.m4s"
    if not image_source.exists() and not payload.expect_image_upload:
        raise HTTPException(422, "默认图片不存在，请选择并上传数字人图片")
    if not voice_source.exists() and not payload.expect_voice_upload:
        raise HTTPException(422, "默认参考音色不存在，请选择并上传参考音频")
    if (
        payload.tts_engine == "indextts2_voice_clone"
        and not payload.expect_emotion_voice_upload
    ):
        raise HTTPException(422, "IndexTTS2 音色与情感克隆版请选择并上传情感参考音频")
    if payload.bgm_enabled and not payload.expect_bgm_upload:
        raise HTTPException(422, "启用视频配乐后请选择并上传背景音乐")
    project_id = uuid.uuid4().hex[:12]
    project_dir = settings.data_dir / "jobs" / project_id
    input_dir = project_dir / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    image_path = input_dir / f"portrait{image_source.suffix or '.png'}"
    voice_path = input_dir / f"voice{voice_source.suffix or '.m4a'}"
    emotion_voice_path = input_dir / "emotion_voice.m4a"
    bgm_path = input_dir / "background_music.mp3"
    if image_source.exists() and not payload.expect_image_upload:
        shutil.copyfile(image_source, image_path)
    if voice_source.exists() and not payload.expect_voice_upload:
        shutil.copyfile(voice_source, voice_path)
    return repository.create(
        {
            "id": project_id,
            "title": payload.title.strip() or "未命名项目",
            "original_script": payload.original_script.strip(),
            "purpose": payload.purpose.strip(),
            "audience": payload.audience.strip(),
            "requested_style": payload.requested_style.strip(),
            "tts_engine": payload.tts_engine,
            "auto_run": payload.auto_run,
            "assets_pending": bool(
                payload.expect_image_upload
                or payload.expect_voice_upload
                or payload.expect_emotion_voice_upload
                or payload.expect_bgm_upload
            ),
            "project_dir": str(project_dir),
            "image_path": str(image_path),
            "voice_path": str(voice_path),
            "emotion_voice_path": (
                str(emotion_voice_path)
                if payload.tts_engine == "indextts2_voice_clone"
                else None
            ),
            "bgm_enabled": payload.bgm_enabled,
            "bgm_path": str(bgm_path) if payload.bgm_enabled else None,
            "bgm_volume": payload.bgm_volume,
            "bgm_ducking": payload.bgm_ducking,
            "bgm_fade_in": payload.bgm_fade_in,
            "bgm_fade_out": payload.bgm_fade_out,
            "subtitle_enabled": payload.subtitle_enabled,
            "subtitle_font_name": payload.subtitle_font_name,
            "subtitle_font_size": payload.subtitle_font_size,
            "subtitle_font_bold": payload.subtitle_font_bold,
            "subtitle_font_color": payload.subtitle_font_color,
            "subtitle_position": payload.subtitle_position,
            "subtitle_custom_position": payload.subtitle_custom_position,
            "subtitle_stroke_color": payload.subtitle_stroke_color,
            "subtitle_stroke_width": payload.subtitle_stroke_width,
            "subtitle_background_enabled": payload.subtitle_background_enabled,
            "subtitle_background_color": payload.subtitle_background_color,
            "subtitle_background_opacity": payload.subtitle_background_opacity,
            "subtitle_max_chars": payload.subtitle_max_chars,
            "video_title_enabled": payload.video_title_enabled,
            "video_title": payload.video_title.strip() or suggest_video_title(payload.original_script),
            "video_title_font_name": payload.video_title_font_name,
            "video_title_font_size": payload.video_title_font_size,
            "video_title_primary_color": payload.video_title_primary_color,
            "video_title_secondary_color": payload.video_title_secondary_color,
            "video_title_position": payload.video_title_position,
            "video_title_stroke_color": payload.video_title_stroke_color,
            "video_title_stroke_width": payload.video_title_stroke_width,
            "status": "CREATED",
            "progress": 0,
            "content_revision": 0,
            "audio_started": False,
            "error": None,
            "script": "",
            "emotion": {},
            "segments": [],
        }
    )


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "ai_enabled": settings.ai_enabled,
        "vision_enabled": settings.vision_enabled,
        "asr_enabled": settings.asr_enabled,
        "asr_provider": "whisper_cli",
        "whisper_executable": settings.whisper_path or settings.whisper_executable,
        "whisper_model": settings.whisper_model,
        "queue_worker": "running"
        if production_queue._worker and not production_queue._worker.done()
        else "stopped",
    }


@app.post("/api/comfyui/check")
async def check_comfyui(payload: ComfyCheckRequest) -> dict[str, Any]:
    try:
        client = ComfyUIClient(payload.url, settings.comfy_timeout_seconds)
        return await client.check(
            required_tts_nodes(payload.tts_engine) | REQUIRED_VIDEO_NODES
        )
    except Exception as exc:
        raise HTTPException(502, f"ComfyUI 检查失败：{exc}") from exc


@app.get("/api/settings")
async def get_app_settings() -> dict[str, str]:
    return {"comfy_url": global_comfy_url()}


@app.patch("/api/settings")
async def patch_app_settings(payload: AppSettingsPatch) -> dict[str, str]:
    try:
        comfy_url = ComfyUIClient.normalize_url(payload.comfy_url)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    repository.set_setting("comfy_url", comfy_url)
    return {"comfy_url": comfy_url}


@app.get("/api/fonts")
async def list_subtitle_fonts() -> list[str]:
    names = ["Microsoft YaHei", "SimHei", "Arial", "Noto Sans CJK SC"]
    font_dir = settings.root / "resource" / "fonts"
    if font_dir.is_dir():
        names.extend(
            path.stem
            for path in font_dir.iterdir()
            if path.is_file() and path.suffix.lower() in {".ttf", ".ttc", ".otf"}
        )
    return list(dict.fromkeys(names))


@app.post("/api/projects")
async def create_project(
    original_script: Annotated[str, Form()],
    title: Annotated[str, Form()] = "未命名项目",
    purpose: Annotated[str, Form()] = "品牌口播",
    audience: Annotated[str, Form()] = "普通观众",
    requested_style: Annotated[str, Form()] = "专业、温和、可信赖",
    tts_engine: Annotated[str, Form()] = "indextts2_legacy",
    auto_run: Annotated[bool, Form()] = False,
    image: Annotated[UploadFile | None, File()] = None,
    voice: Annotated[UploadFile | None, File()] = None,
    emotion_voice: Annotated[UploadFile | None, File()] = None,
    bgm: Annotated[UploadFile | None, File()] = None,
    bgm_enabled: Annotated[bool, Form()] = False,
    bgm_volume: Annotated[float, Form()] = 0.25,
    bgm_ducking: Annotated[bool, Form()] = True,
    bgm_fade_in: Annotated[float, Form()] = 1.5,
    bgm_fade_out: Annotated[float, Form()] = 2.0,
    subtitle_enabled: Annotated[bool, Form()] = True,
    subtitle_font_name: Annotated[str, Form()] = "Microsoft YaHei",
    subtitle_font_size: Annotated[int, Form()] = 64,
    subtitle_font_bold: Annotated[bool, Form()] = True,
    subtitle_font_color: Annotated[str, Form()] = "#FFFFFF",
    subtitle_position: Annotated[str, Form()] = "custom",
    subtitle_custom_position: Annotated[float, Form()] = 73,
    subtitle_stroke_color: Annotated[str, Form()] = "#000000",
    subtitle_stroke_width: Annotated[float, Form()] = 3,
    subtitle_background_enabled: Annotated[bool, Form()] = False,
    subtitle_background_color: Annotated[str, Form()] = "#000000",
    subtitle_background_opacity: Annotated[int, Form()] = 40,
    subtitle_max_chars: Annotated[int, Form()] = 14,
    video_title_enabled: Annotated[bool, Form()] = True,
    video_title: Annotated[str, Form()] = "",
    video_title_font_name: Annotated[str, Form()] = "Microsoft YaHei",
    video_title_font_size: Annotated[int, Form()] = 88,
    video_title_primary_color: Annotated[str, Form()] = "#FFFFFF",
    video_title_secondary_color: Annotated[str, Form()] = "#FFD84D",
    video_title_position: Annotated[float, Form()] = 10,
    video_title_stroke_color: Annotated[str, Form()] = "#000000",
    video_title_stroke_width: Annotated[float, Form()] = 4,
) -> dict[str, Any]:
    if not original_script.strip():
        raise HTTPException(422, "口播文案不能为空")
    if tts_engine not in TTS_ENGINES:
        raise HTTPException(422, "未知音频流程")
    if tts_engine == "indextts2_voice_clone" and emotion_voice is None:
        raise HTTPException(422, "IndexTTS2 音色与情感克隆版需要情感参考音频")
    if bgm_enabled and bgm is None:
        raise HTTPException(422, "启用视频配乐后需要上传背景音乐")
    if subtitle_position not in {"top", "center", "bottom", "custom"}:
        raise HTTPException(422, "未知字幕位置")

    project_id = uuid.uuid4().hex[:12]
    project_dir = settings.data_dir / "jobs" / project_id
    input_dir = project_dir / "input"
    image_source = settings.root / "专业肖像照.png"
    voice_source = settings.root / "40049511080-1-30280.m4s"

    image_suffix = Path(image.filename or "portrait.png").suffix.lower() if image else image_source.suffix
    voice_suffix = Path(voice.filename or "voice.m4s").suffix.lower() if voice else voice_source.suffix
    emotion_voice_suffix = (
        Path(emotion_voice.filename or "emotion_voice.m4a").suffix.lower()
        if emotion_voice
        else ".m4a"
    )
    bgm_suffix = Path(bgm.filename or "background_music.mp3").suffix.lower() if bgm else ".mp3"
    if image_suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise HTTPException(422, "图片仅支持 PNG、JPG、WEBP")
    if voice_suffix not in {".wav", ".flac", ".mp3", ".m4a", ".m4s", ".mp4", ".ogg"}:
        raise HTTPException(422, "参考音频格式不支持")
    if emotion_voice_suffix not in {".wav", ".flac", ".mp3", ".m4a", ".m4s", ".mp4", ".ogg"}:
        raise HTTPException(422, "情感参考音频格式不支持")
    if bgm_suffix not in {".wav", ".flac", ".mp3", ".m4a", ".m4s", ".mp4", ".ogg", ".aac"}:
        raise HTTPException(422, "背景音乐格式不支持")
    image_path, voice_path = input_dir / f"portrait{image_suffix}", input_dir / f"voice{voice_suffix}"
    emotion_voice_path = input_dir / f"emotion_voice{emotion_voice_suffix}"
    bgm_path = input_dir / f"background_music{bgm_suffix}"
    input_dir.mkdir(parents=True, exist_ok=True)
    if image:
        await save_upload(image, image_path, 25 * 1024 * 1024)
    elif image_source.exists():
        shutil.copyfile(image_source, image_path)
    else:
        raise HTTPException(422, "请上传数字人图片")
    if voice:
        await save_upload(voice, voice_path, 100 * 1024 * 1024)
    elif voice_source.exists():
        shutil.copyfile(voice_source, voice_path)
    else:
        raise HTTPException(422, "请上传参考音色")
    if emotion_voice:
        await save_upload(emotion_voice, emotion_voice_path, 100 * 1024 * 1024)
    if bgm:
        await save_upload(bgm, bgm_path, 200 * 1024 * 1024)

    project = repository.create(
        {
            "id": project_id,
            "title": title.strip() or "未命名项目",
            "original_script": original_script.strip(),
            "purpose": purpose.strip(),
            "audience": audience.strip(),
            "requested_style": requested_style.strip(),
            "tts_engine": tts_engine,
            "auto_run": auto_run,
            "project_dir": str(project_dir),
            "image_path": str(image_path),
            "voice_path": str(voice_path),
            "emotion_voice_path": (
                str(emotion_voice_path)
                if tts_engine == "indextts2_voice_clone"
                else None
            ),
            "bgm_enabled": bgm_enabled,
            "bgm_path": str(bgm_path) if bgm_enabled else None,
            "bgm_volume": min(1.0, max(0.0, bgm_volume)),
            "bgm_ducking": bgm_ducking,
            "bgm_fade_in": min(30.0, max(0.0, bgm_fade_in)),
            "bgm_fade_out": min(30.0, max(0.0, bgm_fade_out)),
            "subtitle_enabled": subtitle_enabled,
            "subtitle_font_name": subtitle_font_name.strip() or "Microsoft YaHei",
            "subtitle_font_size": min(160, max(12, subtitle_font_size)),
            "subtitle_font_bold": subtitle_font_bold,
            "subtitle_font_color": subtitle_font_color,
            "subtitle_position": subtitle_position,
            "subtitle_custom_position": min(100, max(0, subtitle_custom_position)),
            "subtitle_stroke_color": subtitle_stroke_color,
            "subtitle_stroke_width": min(12, max(0, subtitle_stroke_width)),
            "subtitle_background_enabled": subtitle_background_enabled,
            "subtitle_background_color": subtitle_background_color,
            "subtitle_background_opacity": min(100, max(0, subtitle_background_opacity)),
            "subtitle_max_chars": min(32, max(6, subtitle_max_chars)),
            "video_title_enabled": video_title_enabled,
            "video_title": video_title.strip() or suggest_video_title(original_script),
            "video_title_font_name": video_title_font_name.strip() or "Microsoft YaHei",
            "video_title_font_size": min(180, max(24, video_title_font_size)),
            "video_title_primary_color": video_title_primary_color,
            "video_title_secondary_color": video_title_secondary_color,
            "video_title_position": min(50, max(0, video_title_position)),
            "video_title_stroke_color": video_title_stroke_color,
            "video_title_stroke_width": min(12, max(0, video_title_stroke_width)),
            "status": "CREATED",
            "progress": 0,
            "content_revision": 0,
            "audio_started": False,
            "error": None,
            "script": "",
            "emotion": {},
            "segments": [],
        }
    )
    return public_project(project)


@app.post("/api/projects/default")
async def create_project_default(payload: ProjectCreate) -> dict[str, Any]:
    return public_project(create_default_project(payload))


@app.get("/api/projects")
async def list_projects() -> list[dict[str, Any]]:
    return [public_project(project) for project in repository.list()]


@app.get("/api/projects/summary")
async def list_project_summaries() -> list[dict[str, Any]]:
    return [public_project_summary(project) for project in repository.list()]


@app.get("/api/projects/{project_id}")
async def project_detail(project_id: str) -> dict[str, Any]:
    return public_project(get_project(project_id))


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str) -> dict[str, Any]:
    project = get_project(project_id)
    if repository.active_tasks_for_project(project_id):
        raise HTTPException(409, "项目仍在任务队列中，请先取消或等待任务完成")
    running = runner.tasks.get(project_id)
    if running and not running.done():
        raise HTTPException(409, "项目任务正在执行，请等待任务结束后再删除")
    project_dir = safe_project_directory(project)
    files_removed = False
    if project_dir.exists():
        if not project_dir.is_dir():
            raise HTTPException(409, "项目路径不是目录，已拒绝删除")
        shutil.rmtree(project_dir)
        files_removed = True
    if not repository.delete(project_id):
        raise HTTPException(404, "项目不存在")
    repository.delete_tasks_for_project(project_id)
    runner.tasks.pop(project_id, None)
    return {"id": project_id, "deleted": True, "files_removed": files_removed}


@app.post("/api/projects/{project_id}/enqueue")
async def enqueue_project(project_id: str) -> dict[str, Any]:
    get_project(project_id)
    try:
        task = production_queue.enqueue(project_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc
    queued = [
        item for item in repository.list_tasks() if item["status"] == "QUEUED"
    ]
    position = next(
        (index + 1 for index, item in enumerate(queued) if item["id"] == task["id"]),
        None,
    )
    return public_task(task, position)


@app.get("/api/tasks")
async def list_production_tasks(limit: int = 100) -> list[dict[str, Any]]:
    tasks = [
        task
        for task in repository.list_tasks(max(1, min(limit, 500)))
        if task["status"] != "COMPLETED"
    ]
    queued_ids = [
        task["id"] for task in tasks if task["status"] == "QUEUED"
    ]
    positions = {task_id: index + 1 for index, task_id in enumerate(queued_ids)}
    return [public_task(task, positions.get(task["id"])) for task in tasks]


@app.get("/api/tasks/summary")
async def list_production_task_summaries(limit: int = 100) -> list[dict[str, Any]]:
    tasks = [
        task for task in repository.list_tasks(max(1, min(limit, 500)))
        if task["status"] != "COMPLETED"
    ]
    queued_ids = [task["id"] for task in tasks if task["status"] == "QUEUED"]
    positions = {task_id: index + 1 for index, task_id in enumerate(queued_ids)}
    return [public_task_summary(task, positions.get(task["id"])) for task in tasks]


@app.get("/api/events/tasks")
async def stream_task_events(request: Request) -> StreamingResponse:
    async def stream():
        try:
            yield "retry: 3000\ndata: {\"entity\":\"connected\"}\n\n"
            async with event_broker.subscribe() as queue:
                while not await request.is_disconnected():
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=20)
                        yield EventBroker.encode_sse(event)
                    except asyncio.TimeoutError:
                        yield ": heartbeat\n\n"
        except asyncio.CancelledError:
            # Uvicorn cancels long-lived streams after the short graceful
            # shutdown window. Treat that as a normal client disconnect.
            return

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/tasks/{task_id}")
async def production_task_detail(task_id: str) -> dict[str, Any]:
    task = repository.get_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    queued = [
        item["id"]
        for item in repository.list_tasks()
        if item["status"] == "QUEUED"
    ]
    position = queued.index(task_id) + 1 if task_id in queued else None
    return public_task(task, position)


@app.post("/api/tasks/{task_id}/cancel")
async def cancel_production_task(task_id: str) -> dict[str, Any]:
    try:
        task = await production_queue.cancel(task_id)
    except KeyError as exc:
        raise HTTPException(404, "任务不存在") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    project = repository.get(task["project_id"])
    if project:
        repository.update(
            task["project_id"], status="QUEUE_CANCELLED", progress=0
        )
    return public_task(task)


@app.patch("/api/tasks/{task_id}")
async def patch_production_task(
    task_id: str, patch: TaskPatch
) -> dict[str, Any]:
    task = repository.get_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    project = repository.get(task["project_id"])
    if not project:
        raise HTTPException(404, "任务对应的项目不存在")
    changes = patch.model_dump(exclude_none=True)
    task_payload_changes: dict[str, Any] = {}
    project_changes: dict[str, Any] = {}
    if "title" in changes:
        title = changes["title"].strip()
        if not title:
            raise HTTPException(422, "任务名称不能为空")
        project_changes["title"] = title
        task_payload_changes["title"] = title
    if "original_script" in changes:
        original_script = changes["original_script"].strip()
        if not original_script:
            raise HTTPException(422, "口播稿不能为空")
        if not can_edit_task_script(task, project):
            raise HTTPException(409, "音频已经开始生成，口播稿不可修改")
        if original_script != project.get("original_script", "").strip():
            project_changes.update(
                {
                    "original_script": original_script,
                    "content_revision": int(project.get("content_revision") or 0) + 1,
                    "script": "",
                    "style": "",
                    "emotion": {},
                    "segments": [],
                    "alignment": None,
                    "audio_path": None,
                    "audio_duration": None,
                    "video_path": None,
                    "raw_video_path": None,
                    "bgm_video_path": None,
                    "subtitle_video_path": None,
                    "subtitle_path": None,
                    "subtitle_ass_path": None,
                    "status": (
                        "ANALYZING_IMAGE"
                        if task.get("status") == "RUNNING"
                        else "QUEUE_WAITING"
                        if task.get("status") == "QUEUED"
                        else "CREATED"
                    ),
                    "progress": 5 if task.get("status") == "RUNNING" else 0,
                    "error": None,
                }
            )
            task_payload_changes["original_script"] = original_script
    if project_changes:
        project = repository.update(task["project_id"], **project_changes)
    if task_payload_changes:
        task = repository.update_task_payload(task_id, **task_payload_changes)
    return public_task(task)


@app.post("/api/tasks/{task_id}/retry")
async def retry_production_task(task_id: str) -> dict[str, Any]:
    existing = repository.get_task(task_id)
    if not existing:
        raise HTTPException(404, "任务不存在")
    manual_run = runner.tasks.get(existing["project_id"])
    if manual_run and not manual_run.done():
        raise HTTPException(409, "项目当前正在手动执行，完成后才能重试历史队列任务")
    try:
        task = repository.retry_task(task_id)
    except KeyError as exc:
        raise HTTPException(404, "任务不存在") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    project = repository.get(task["project_id"])
    if project:
        repository.update(
            task["project_id"], status="QUEUE_WAITING", progress=0, error=None
        )
    production_queue.notify()
    return public_task(task)


@app.delete("/api/tasks/{task_id}")
async def delete_production_task(task_id: str) -> dict[str, Any]:
    existing = repository.get_task(task_id)
    if not existing:
        raise HTTPException(404, "任务不存在")
    project_id = existing["project_id"]
    try:
        for active in repository.active_tasks_for_project(project_id):
            await production_queue.cancel(active["id"])
        manual_run = runner.tasks.get(project_id)
        if manual_run and not manual_run.done():
            await runner.cancel_project(project_id)
    except KeyError as exc:
        raise HTTPException(404, "任务不存在") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    project = repository.get(project_id)
    files_removed = False
    if project:
        project_dir = safe_project_directory(project)
        if project_dir.exists():
            if not project_dir.is_dir():
                raise HTTPException(409, "项目路径不是目录，已拒绝删除")
            shutil.rmtree(project_dir)
            files_removed = True
        repository.delete(project_id)
    repository.delete_tasks_for_project(project_id)
    runner.tasks.pop(project_id, None)
    return {
        "id": task_id,
        "project_id": project_id,
        "deleted": True,
        "project_deleted": bool(project),
        "files_removed": files_removed,
    }


@app.patch("/api/projects/{project_id}")
async def patch_project(project_id: str, patch: ProjectPatch) -> dict[str, Any]:
    project = get_project(project_id)
    changes = patch.model_dump(exclude_none=True)
    active_tasks = repository.active_tasks_for_project(project_id)
    active_task = active_tasks[0] if active_tasks else None
    original_script_changed = False
    if "original_script" in changes:
        original_script = changes["original_script"].strip()
        if not original_script:
            raise HTTPException(422, "口播稿不能为空")
        editable = (
            can_edit_task_script(active_task, project)
            if active_task
            else can_edit_project_script(project)
        )
        if not editable:
            raise HTTPException(409, "音频已经开始生成，口播稿不可修改")
        original_script_changed = (
            original_script != project.get("original_script", "").strip()
        )
        changes["original_script"] = original_script
        if original_script_changed:
            changes.update(
                {
                    "content_revision": int(project.get("content_revision") or 0) + 1,
                    "script": "",
                    "style": "",
                    "emotion": {},
                    "segments": [],
                    "alignment": None,
                    "audio_path": None,
                    "audio_duration": None,
                    "video_path": None,
                    "raw_video_path": None,
                    "bgm_video_path": None,
                    "subtitle_video_path": None,
                    "subtitle_path": None,
                    "subtitle_ass_path": None,
                    "status": (
                        "ANALYZING_IMAGE"
                        if active_task and active_task["status"] == "RUNNING"
                        else "QUEUE_WAITING"
                        if active_task and active_task["status"] == "QUEUED"
                        else "CREATED"
                    ),
                    "progress": (
                        5
                        if active_task and active_task["status"] == "RUNNING"
                        else 0
                    ),
                    "error": None,
                }
            )
    if "emotion" in changes:
        changes["emotion"] = patch.emotion.model_dump() if patch.emotion else {}
    if "segments" in changes:
        changes["segments"] = [segment.model_dump() for segment in patch.segments or []]
    engine_changed = (
        "tts_engine" in changes
        and changes["tts_engine"]
        != project.get("tts_engine", "indextts2_legacy")
    )
    script_changed = (
        "script" in changes
        and changes["script"].strip() != project.get("script", "").strip()
    )
    if original_script_changed:
        pass
    elif script_changed or engine_changed:
        changes.update(
            {
                "audio_path": None,
                "video_path": None,
                "raw_video_path": None,
                "bgm_video_path": None,
                "subtitle_video_path": None,
                "subtitle_path": None,
                "subtitle_ass_path": None,
                "audio_duration": None,
                "segments": [],
                "status": "SCRIPT_READY",
                "progress": 0,
            }
        )
    elif "segments" in changes:
        changes.update(
            {
                "video_path": None,
                "raw_video_path": None,
                "bgm_video_path": None,
                "subtitle_video_path": None,
                "subtitle_path": None,
                "subtitle_ass_path": None,
                "status": "PLAN_READY",
                "progress": 100,
            }
        )
    bgm_settings_changed = bool(
        {"bgm_enabled", "bgm_volume", "bgm_ducking", "bgm_fade_in", "bgm_fade_out"}
        & set(changes)
    )
    if bgm_settings_changed:
        source_video = project.get("subtitle_video_path") or project.get("raw_video_path") or (
            project.get("video_path") if not project.get("bgm_video_path") else None
        )
        changes["bgm_video_path"] = None
        if source_video and Path(source_video).is_file():
            changes.update(
                {
                    "video_path": source_video,
                    "status": "VIDEO_READY" if changes.get("bgm_enabled", project.get("bgm_enabled")) else "COMPLETED",
                    "progress": 100,
                    "error": None,
                }
            )
    subtitle_fields = {
        "subtitle_enabled",
        "subtitle_font_name",
        "subtitle_font_size",
        "subtitle_font_bold",
        "subtitle_font_color",
        "subtitle_position",
        "subtitle_custom_position",
        "subtitle_stroke_color",
        "subtitle_stroke_width",
        "subtitle_background_enabled",
        "subtitle_background_color",
        "subtitle_background_opacity",
        "subtitle_max_chars",
        "video_title_enabled",
        "video_title",
        "video_title_font_name",
        "video_title_font_size",
        "video_title_primary_color",
        "video_title_secondary_color",
        "video_title_position",
        "video_title_stroke_color",
        "video_title_stroke_width",
    }
    if subtitle_fields & set(changes):
        raw_video = project.get("raw_video_path")
        changes.update(
            {
                "subtitle_video_path": None,
                "subtitle_path": None,
                "subtitle_ass_path": None,
                "bgm_video_path": None,
            }
        )
        if raw_video and Path(raw_video).is_file():
            subtitle_enabled = changes.get("subtitle_enabled", project.get("subtitle_enabled"))
            title_enabled = changes.get("video_title_enabled", project.get("video_title_enabled", True))
            enabled = subtitle_enabled or title_enabled
            needs_bgm = changes.get("bgm_enabled", project.get("bgm_enabled"))
            changes.update(
                {
                    "video_path": raw_video,
                    "status": "VIDEO_READY" if enabled or needs_bgm else "COMPLETED",
                    "progress": 100,
                    "error": None,
                }
            )
    updated = repository.update(project_id, **changes)
    if original_script_changed and active_task:
        repository.update_task_payload(
            active_task["id"], original_script=changes["original_script"]
        )
    return public_project(updated)


@app.post("/api/projects/{project_id}/assets/{kind}")
async def upload_project_asset(
    project_id: str, kind: str, file: Annotated[UploadFile, File()]
) -> dict[str, Any]:
    project = get_project(project_id)
    if kind == "image":
        suffix = Path(file.filename or "portrait.png").suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise HTTPException(422, "图片仅支持 PNG、JPG、WEBP")
        destination = Path(project["project_dir"]) / "input" / f"portrait{suffix}"
        await save_upload(file, destination, 25 * 1024 * 1024)
        changes = {
            "image_path": str(destination),
            "status": "CREATED",
            "image_analysis": None,
            "video_path": None,
            "raw_video_path": None,
            "bgm_video_path": None,
            "subtitle_video_path": None,
            "subtitle_path": None,
            "subtitle_ass_path": None,
        }
    elif kind == "voice":
        suffix = Path(file.filename or "voice.m4s").suffix.lower()
        if suffix not in {".wav", ".flac", ".mp3", ".m4a", ".m4s", ".mp4", ".ogg"}:
            raise HTTPException(422, "参考音频格式不支持")
        destination = Path(project["project_dir"]) / "input" / f"voice{suffix}"
        await save_upload(file, destination, 100 * 1024 * 1024)
        changes = {
            "voice_path": str(destination),
            "audio_path": None,
            "video_path": None,
            "raw_video_path": None,
            "bgm_video_path": None,
            "subtitle_video_path": None,
            "subtitle_path": None,
            "subtitle_ass_path": None,
            "status": "CREATED",
        }
    elif kind == "emotion_voice":
        suffix = Path(file.filename or "emotion_voice.m4a").suffix.lower()
        if suffix not in {".wav", ".flac", ".mp3", ".m4a", ".m4s", ".mp4", ".ogg"}:
            raise HTTPException(422, "情感参考音频格式不支持")
        destination = (
            Path(project["project_dir"]) / "input" / f"emotion_voice{suffix}"
        )
        await save_upload(file, destination, 100 * 1024 * 1024)
        changes = {
            "emotion_voice_path": str(destination),
            "audio_path": None,
            "video_path": None,
            "raw_video_path": None,
            "bgm_video_path": None,
            "subtitle_video_path": None,
            "subtitle_path": None,
            "subtitle_ass_path": None,
            "status": "CREATED",
        }
    elif kind == "bgm":
        suffix = Path(file.filename or "background_music.mp3").suffix.lower()
        if suffix not in {".wav", ".flac", ".mp3", ".m4a", ".m4s", ".mp4", ".ogg", ".aac"}:
            raise HTTPException(422, "背景音乐格式不支持")
        destination = Path(project["project_dir"]) / "input" / f"background_music{suffix}"
        await save_upload(file, destination, 200 * 1024 * 1024)
        source_video = project.get("subtitle_video_path") or project.get("raw_video_path")
        changes = {
            "bgm_enabled": True,
            "bgm_path": str(destination),
            "bgm_video_path": None,
            "video_path": source_video or project.get("video_path"),
            "status": "VIDEO_READY" if source_video and Path(source_video).is_file() else project.get("status", "CREATED"),
            "error": None,
        }
    else:
        raise HTTPException(404, "未知素材类型")
    return public_project(repository.update(project_id, **changes))


@app.post("/api/projects/{project_id}/run/{stage}")
async def run_stage(project_id: str, stage: str) -> dict[str, Any]:
    project = get_project(project_id)
    if production_stage_locked(project, stage):
        raise HTTPException(409, "项目已生成成片，不能再执行 AI 导演分析、音频生成或视频生成")
    if repository.active_tasks_for_project(project_id):
        raise HTTPException(409, "项目已在自动生产队列中，不能同时手动执行")
    try:
        runner.start(project_id, stage)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return public_project(get_project(project_id))


@app.get("/api/projects/{project_id}/thumbnail")
async def project_thumbnail(project_id: str) -> FileResponse:
    project = get_project(project_id)
    source = Path(str(project.get("image_path") or ""))
    if not source.is_file():
        raise HTTPException(404, "项目没有可用的数字人图片")
    project_dir = safe_project_directory(project)
    destination = project_dir / "cache" / "portrait-240x320.webp"
    try:
        thumbnail = await asyncio.to_thread(build_project_thumbnail, source, destination)
    except (OSError, ValueError) as exc:
        raise HTTPException(422, f"无法生成项目缩略图：{exc}") from exc
    return FileResponse(
        thumbnail,
        media_type="image/webp",
        headers={"Cache-Control": "public, max-age=86400, immutable"},
    )


@app.get("/api/projects/{project_id}/files/{kind}")
async def project_file(project_id: str, kind: str) -> FileResponse:
    project = get_project(project_id)
    field = {
        "image": "image_path",
        "voice": "voice_path",
        "audio": "audio_path",
        "bgm": "bgm_path",
        "video": "video_path",
        "video_raw": "raw_video_path",
        "video_bgm": "bgm_video_path",
        "video_subtitled": "subtitle_video_path",
        "subtitle": "subtitle_path",
        "subtitle_ass": "subtitle_ass_path",
    }.get(kind)
    if kind == "video_raw" and not project.get("raw_video_path") and not project.get("bgm_video_path"):
        field = "video_path"
    if not field or not project.get(field):
        raise HTTPException(404, "文件不存在")
    path = Path(project[field])
    if not path.exists():
        raise HTTPException(404, "文件不存在")
    return FileResponse(path, filename=path.name)


@app.get("/")
async def index() -> dict[str, Any]:
    return {"name": "数字人工厂 API", "docs": "/docs", "health": "/api/health"}
