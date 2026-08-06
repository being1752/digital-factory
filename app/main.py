from __future__ import annotations

import os
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .ai_director import AIDirector
from .alignment import SpeechAlignmentService
from .comfyui import ComfyUIClient
from .config import settings
from .orchestrator import TaskRunner
from .production_queue import ProductionQueue
from .repository import ProjectRepository
from .schemas import (
    AppSettingsPatch,
    ComfyCheckRequest,
    ProjectCreate,
    ProjectPatch,
    TTS_ENGINES,
)
from .workflows import REQUIRED_VIDEO_NODES, WorkflowCompiler, required_tts_nodes


@asynccontextmanager
async def lifespan(_: FastAPI):
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
director = AIDirector(settings)
aligner = SpeechAlignmentService(settings)
compiler = WorkflowCompiler(settings.root)
runner = TaskRunner(settings, repository, director, aligner, compiler)
production_queue = ProductionQueue(repository, runner)


def global_comfy_url() -> str:
    return repository.get_setting("comfy_url", settings.default_comfy_url)


def public_project(project: dict[str, Any]) -> dict[str, Any]:
    hidden = {
        "comfy_url",
        "image_path",
        "voice_path",
        "emotion_voice_path",
        "audio_path",
        "video_path",
        "project_dir",
    }
    result = {key: value for key, value in project.items() if key not in hidden}
    def exists(field: str) -> bool:
        value = project.get(field)
        return bool(value and Path(value).exists())

    result["has_image"] = exists("image_path")
    result["has_voice"] = exists("voice_path")
    result["has_emotion_voice"] = exists("emotion_voice_path")
    result["has_audio"] = exists("audio_path")
    result["has_video"] = exists("video_path")
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
        result["project_status"] = project.get("status", "")
        result["project_progress"] = project.get("progress", 0)
        result["display_progress"] = project.get("progress", task.get("progress", 0))
        result["has_audio"] = bool(
            project.get("audio_path") and Path(project["audio_path"]).is_file()
        )
        result["has_video"] = bool(
            project.get("video_path") and Path(project["video_path"]).is_file()
        )
    else:
        result["display_progress"] = task.get("progress", 0)
    if queue_position is not None:
        result["queue_position"] = queue_position
    return result


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
    project_id = uuid.uuid4().hex[:12]
    project_dir = settings.data_dir / "jobs" / project_id
    input_dir = project_dir / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    image_path = input_dir / f"portrait{image_source.suffix or '.png'}"
    voice_path = input_dir / f"voice{voice_source.suffix or '.m4a'}"
    emotion_voice_path = input_dir / "emotion_voice.m4a"
    if image_source.exists():
        shutil.copyfile(image_source, image_path)
    if voice_source.exists():
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
            "project_dir": str(project_dir),
            "image_path": str(image_path),
            "voice_path": str(voice_path),
            "emotion_voice_path": (
                str(emotion_voice_path)
                if payload.tts_engine == "indextts2_voice_clone"
                else None
            ),
            "status": "CREATED",
            "progress": 0,
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
) -> dict[str, Any]:
    if not original_script.strip():
        raise HTTPException(422, "口播文案不能为空")
    if tts_engine not in TTS_ENGINES:
        raise HTTPException(422, "未知音频流程")
    if tts_engine == "indextts2_voice_clone" and emotion_voice is None:
        raise HTTPException(422, "IndexTTS2 音色与情感克隆版需要情感参考音频")

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
    if image_suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise HTTPException(422, "图片仅支持 PNG、JPG、WEBP")
    if voice_suffix not in {".wav", ".flac", ".mp3", ".m4a", ".m4s", ".mp4", ".ogg"}:
        raise HTTPException(422, "参考音频格式不支持")
    if emotion_voice_suffix not in {".wav", ".flac", ".mp3", ".m4a", ".m4s", ".mp4", ".ogg"}:
        raise HTTPException(422, "情感参考音频格式不支持")
    image_path, voice_path = input_dir / f"portrait{image_suffix}", input_dir / f"voice{voice_suffix}"
    emotion_voice_path = input_dir / f"emotion_voice{emotion_voice_suffix}"
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
            "status": "CREATED",
            "progress": 0,
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
    tasks = repository.list_tasks(max(1, min(limit, 500)))
    queued_ids = [
        task["id"] for task in tasks if task["status"] == "QUEUED"
    ]
    positions = {task_id: index + 1 for index, task_id in enumerate(queued_ids)}
    return [public_task(task, positions.get(task["id"])) for task in tasks]


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
        task = repository.cancel_task(task_id)
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
    try:
        task = repository.delete_task(task_id)
    except KeyError as exc:
        raise HTTPException(404, "任务不存在") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "id": task_id,
        "project_id": task["project_id"],
        "deleted": True,
    }


@app.patch("/api/projects/{project_id}")
async def patch_project(project_id: str, patch: ProjectPatch) -> dict[str, Any]:
    project = get_project(project_id)
    changes = patch.model_dump(exclude_none=True)
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
    if script_changed or engine_changed:
        changes.update(
            {
                "audio_path": None,
                "video_path": None,
                "audio_duration": None,
                "segments": [],
                "status": "SCRIPT_READY",
                "progress": 0,
            }
        )
    elif "segments" in changes:
        changes.update({"video_path": None, "status": "PLAN_READY", "progress": 100})
    return public_project(repository.update(project_id, **changes))


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
        changes = {"image_path": str(destination), "status": "CREATED", "image_analysis": None}
    elif kind == "voice":
        suffix = Path(file.filename or "voice.m4s").suffix.lower()
        if suffix not in {".wav", ".flac", ".mp3", ".m4a", ".m4s", ".mp4", ".ogg"}:
            raise HTTPException(422, "参考音频格式不支持")
        destination = Path(project["project_dir"]) / "input" / f"voice{suffix}"
        await save_upload(file, destination, 100 * 1024 * 1024)
        changes = {"voice_path": str(destination), "audio_path": None, "video_path": None, "status": "CREATED"}
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
            "status": "CREATED",
        }
    else:
        raise HTTPException(404, "未知素材类型")
    return public_project(repository.update(project_id, **changes))


@app.post("/api/projects/{project_id}/run/{stage}")
async def run_stage(project_id: str, stage: str) -> dict[str, Any]:
    get_project(project_id)
    if repository.active_tasks_for_project(project_id):
        raise HTTPException(409, "项目已在自动生产队列中，不能同时手动执行")
    try:
        runner.start(project_id, stage)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return public_project(get_project(project_id))


@app.get("/api/projects/{project_id}/files/{kind}")
async def project_file(project_id: str, kind: str) -> FileResponse:
    project = get_project(project_id)
    field = {"image": "image_path", "voice": "voice_path", "audio": "audio_path", "video": "video_path"}.get(kind)
    if not field or not project.get(field):
        raise HTTPException(404, "文件不存在")
    path = Path(project[field])
    if not path.exists():
        raise HTTPException(404, "文件不存在")
    return FileResponse(path, filename=path.name)


@app.get("/")
async def index() -> dict[str, Any]:
    return {"name": "数字人工厂 API", "docs": "/docs", "health": "/api/health"}
