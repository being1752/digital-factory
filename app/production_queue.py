from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .orchestrator import TaskRunner
from .repository import ProjectRepository, now_iso


class ProductionQueue:
    """Persistent FIFO queue with one GPU-safe worker."""

    def __init__(self, repository: ProjectRepository, runner: TaskRunner):
        self.repository = repository
        self.runner = runner
        self._wake = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None
        self._active_task_id: str | None = None
        self._active_execution: asyncio.Task[None] | None = None
        self._stopping = False
        self.analysis_retry_seconds = 3.0

    async def start(self) -> None:
        if self._worker and not self._worker.done():
            return
        self.repository.recover_running_tasks()
        self._stopping = False
        self._worker = asyncio.create_task(self._run(), name="production-queue-worker")
        self._wake.set()

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if not self._worker:
            return
        self._worker.cancel()
        try:
            await self._worker
        except asyncio.CancelledError:
            pass
        self._worker = None

    def enqueue(self, project_id: str, priority: int = 0) -> dict[str, Any]:
        project = self.repository.get(project_id)
        if not project:
            raise KeyError(project_id)
        assets_pending = bool(project.get("assets_pending"))
        for field, label in (
            ("image_path", "数字人图片"),
            ("voice_path", "参考音频"),
            ("original_script", "口播稿"),
        ):
            value = project.get(field)
            if not value:
                raise ValueError(f"缺少可用的{label}")
            if (
                field.endswith("_path")
                and not Path(value).is_file()
                and not assets_pending
            ):
                raise ValueError(f"缺少可用的{label}")
        if project.get("tts_engine") == "indextts2_voice_clone":
            emotion_voice = Path(project.get("emotion_voice_path") or "")
            if not emotion_voice.is_file() and not assets_pending:
                raise ValueError("IndexTTS2 音色与情感克隆版缺少可用的情感参考音频")
        if project.get("bgm_enabled"):
            bgm = Path(project.get("bgm_path") or "")
            if not bgm.is_file() and not assets_pending:
                raise ValueError("启用视频配乐后缺少可用的背景音乐")
        if project.get("auto_run") is False:
            raise ValueError("项目未勾选全自动执行，请在项目详情中手动操作")
        task = self.repository.enqueue_task(project_id, project, priority)
        self.repository.update(
            project_id, status="QUEUE_WAITING", progress=0, error=None
        )
        self._wake.set()
        return task

    def notify(self) -> None:
        self._wake.set()

    async def cancel(self, task_id: str) -> dict[str, Any]:
        task = self.repository.get_task(task_id)
        if not task:
            raise KeyError(task_id)
        was_running = task["status"] == "RUNNING"
        if task["status"] in {"QUEUED", "RUNNING"}:
            task = self.repository.cancel_task(task_id)
            if self._active_task_id == task_id and self._active_execution:
                self._active_execution.cancel()
                try:
                    await self._active_execution
                except asyncio.CancelledError:
                    pass
            if was_running:
                await self.runner.cancel_project(task["project_id"])
            project = self.repository.get(task["project_id"])
            if project:
                self.repository.update(
                    task["project_id"], status="QUEUE_CANCELLED", progress=0
                )
        self._wake.set()
        return self.repository.get_task(task_id) or task

    async def _run(self) -> None:
        while not self._stopping:
            self._wake.clear()
            task = self.repository.claim_next_task()
            if not task:
                await self._wake.wait()
                continue
            self._active_task_id = task["id"]
            self._active_execution = asyncio.create_task(self._execute(task))
            try:
                await self._active_execution
            except asyncio.CancelledError:
                if self._stopping:
                    raise
            finally:
                self._active_task_id = None
                self._active_execution = None

    async def _execute(self, task: dict[str, Any]) -> None:
        task_id, project_id = task["id"], task["project_id"]
        try:
            current_task = self.repository.get_task(task_id)
            if not current_task or current_task["status"] == "CANCELLED":
                return
            project = self.repository.get(project_id)
            if not project:
                raise FileNotFoundError("队列任务对应的项目不存在")
            frozen = {
                key: value
                for key, value in (task.get("snapshot") or {}).items()
                if value is not None
                and key
                not in {
                    "title",
                    "image_path",
                    "voice_path",
                    "emotion_voice_path",
                    "bgm_path",
                }
            }
            if frozen:
                project = self.repository.update(project_id, **frozen)

            while True:
                project = self.repository.get(project_id)
                if not project:
                    raise FileNotFoundError("队列任务对应的项目不存在")
                required_paths = [
                    project.get("image_path"),
                    project.get("voice_path"),
                ]
                if project.get("tts_engine") == "indextts2_voice_clone":
                    required_paths.append(project.get("emotion_voice_path"))
                if project.get("bgm_enabled"):
                    required_paths.append(project.get("bgm_path"))
                if all(value and Path(value).is_file() for value in required_paths):
                    if project.get("assets_pending"):
                        project = self.repository.update(
                            project_id, assets_pending=False
                        )
                    break
                current_task = self.repository.get_task(task_id)
                if not current_task or current_task["status"] == "CANCELLED":
                    return
                if current_task.get("stage") != "UPLOADING_ASSETS":
                    self.repository.update_task(
                        task_id, stage="UPLOADING_ASSETS", progress=1, error=None
                    )
                if project.get("status") != "UPLOADING_ASSETS":
                    self.repository.update(
                        project_id,
                        status="UPLOADING_ASSETS",
                        progress=1,
                        error=None,
                    )
                await asyncio.sleep(0.25)

            analysis_attempt = 0
            while not project.get("script") or not project.get("image_analysis"):
                current_task = self.repository.get_task(task_id)
                if current_task and current_task["status"] == "CANCELLED":
                    self.repository.update(
                        project_id, status="QUEUE_CANCELLED", progress=0
                    )
                    return
                analysis_attempt += 1
                self.repository.update_task(
                    task_id, stage="ANALYZING", progress=5, error=None
                )
                try:
                    await self.runner.analyze(project_id)
                    current_task = self.repository.get_task(task_id)
                    if current_task and current_task["status"] == "CANCELLED":
                        self.repository.update(
                            project_id, status="QUEUE_CANCELLED", progress=0
                        )
                        return
                    project = self.repository.get(project_id) or project
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    error = {
                        "type": type(exc).__name__,
                        "message": str(exc),
                        "attempt": analysis_attempt,
                        "retry_after_seconds": self.analysis_retry_seconds,
                    }
                    self.repository.update_task(
                        task_id,
                        stage="ANALYSIS_RETRYING",
                        progress=5,
                        error=error,
                    )
                    self.repository.update(
                        project_id,
                        status="ANALYSIS_RETRYING",
                        progress=5,
                        error=error,
                    )
                    await asyncio.sleep(self.analysis_retry_seconds)
                    project = self.repository.get(project_id) or project

            audio_ready = bool(
                project.get("audio_path")
                and Path(project["audio_path"]).is_file()
                and project.get("segments")
            )
            current_task = self.repository.get_task(task_id)
            if not current_task or current_task["status"] == "CANCELLED":
                return
            if not audio_ready:
                self.repository.update_task(
                    task_id, stage="GENERATING_AUDIO", progress=25, error=None
                )
                await self.runner.generate_audio(project_id)
                project = self.repository.get(project_id) or project

            current_task = self.repository.get_task(task_id)
            if not current_task or current_task["status"] == "CANCELLED":
                return
            video_ready = bool(
                project.get("video_path") and Path(project["video_path"]).is_file()
            )
            if not video_ready:
                self.repository.update_task(
                    task_id, stage="GENERATING_VIDEO", progress=60
                )
                await self.runner.generate_video(project_id)

            project = self.repository.get(project_id) or project
            subtitle_ready = bool(
                project.get("subtitle_video_path")
                and Path(project["subtitle_video_path"]).is_file()
            )
            overlay_enabled = bool(project.get("subtitle_enabled") or project.get("video_title_enabled", False))
            if overlay_enabled and not subtitle_ready:
                self.repository.update_task(
                    task_id, stage="BURNING_SUBTITLES", progress=90, error=None
                )
                await self.runner.render_subtitles(project_id)
                project = self.repository.get(project_id) or project

            bgm_ready = bool(
                project.get("bgm_video_path")
                and Path(project["bgm_video_path"]).is_file()
            )
            if project.get("bgm_enabled") and not bgm_ready:
                self.repository.update_task(
                    task_id, stage="MIXING_BGM", progress=95, error=None
                )
                await self.runner.mix_background_music(project_id)

            self.repository.update_task(
                task_id,
                status="COMPLETED",
                stage="COMPLETED",
                progress=100,
                completed_at=now_iso(),
                error=None,
            )
        except asyncio.CancelledError:
            current = self.repository.get_task(task_id)
            if current and current["status"] not in {"CANCELLED"}:
                self.repository.update_task(
                    task_id,
                    status="QUEUED",
                    stage="RECOVERING",
                    progress=0,
                    error=None,
                )
            raise
        except Exception as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
            self.repository.update_task(
                task_id,
                status="FAILED",
                stage="FAILED",
                completed_at=now_iso(),
                error=error,
            )
            project = self.repository.get(project_id)
            if project:
                self.repository.update(
                    project_id, status="ERROR", error=error
                )
