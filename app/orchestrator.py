from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path
from typing import Any, Awaitable, Callable

from .ai_director import AIDirector
from .alignment import SpeechAlignmentService
from .audio import audio_duration
from .comfyui import ComfyUIClient, ComfyUIError
from .config import Settings
from .postproduction import BackgroundMusicMixer
from .repository import ProjectRepository
from .subtitles import SubtitleDocument, SubtitleRenderer
from .workflows import WorkflowCompiler


class TaskRunner:
    def __init__(
        self,
        settings: Settings,
        repository: ProjectRepository,
        director: AIDirector,
        aligner: SpeechAlignmentService,
        compiler: WorkflowCompiler,
    ):
        self.settings = settings
        self.repository = repository
        self.director = director
        self.aligner = aligner
        self.compiler = compiler
        self.music_mixer = BackgroundMusicMixer()
        self.subtitle_renderer = SubtitleRenderer()
        self.tasks: dict[str, asyncio.Task[None]] = {}

    def _comfy_url(self) -> str:
        return self.repository.get_setting(
            "comfy_url", self.settings.default_comfy_url
        )

    def start(self, project_id: str, stage: str) -> None:
        existing = self.tasks.get(project_id)
        if existing and not existing.done():
            raise RuntimeError("该项目已有任务正在执行")
        handlers: dict[str, Callable[[str], Awaitable[None]]] = {
            "analyze": self.analyze,
            "audio": self.generate_audio,
            "align": self.align_audio,
            "video": self.generate_video,
            "subtitle": self.render_subtitles,
            "bgm": self.mix_background_music,
        }
        if stage not in handlers:
            raise ValueError(f"未知任务阶段：{stage}")
        self.repository.update(project_id, status=f"{stage.upper()}_QUEUED", error=None)
        task = asyncio.create_task(self._guard(project_id, handlers[stage]))
        self.tasks[project_id] = task

    async def _guard(
        self, project_id: str, handler: Callable[[str], Awaitable[None]]
    ) -> None:
        try:
            await handler(project_id)
        except Exception as exc:
            self.repository.update(
                project_id,
                status="ERROR",
                error={"type": type(exc).__name__, "message": str(exc)},
            )

    async def analyze(self, project_id: str) -> None:
        project = self._project(project_id)
        content_revision = int(project.get("content_revision") or 0)
        self.repository.update(project_id, status="ANALYZING_IMAGE", progress=5)
        result = await self.director.analyze_and_write(
            Path(project["image_path"]),
            project["original_script"],
            project.get("purpose", ""),
            project.get("audience", ""),
            project.get("requested_style", ""),
        )
        latest = self._project(project_id)
        if int(latest.get("content_revision") or 0) != content_revision:
            return
        plan_path = self._project_dir(project) / "plan" / "image_analysis.json"
        self._write_json(plan_path, result)
        self.repository.update(
            project_id,
            status="SCRIPT_READY",
            progress=100,
            error=None,
            image_analysis=result["image_analysis"],
            script=result["script"],
            style=result["style"],
            emotion=result["emotion"],
            ai_mode=result["ai_mode"],
        )

    async def generate_audio(self, project_id: str) -> None:
        project = self._project(project_id)
        if not project.get("script"):
            await self.analyze(project_id)
            project = self._project(project_id)
        self.repository.update(project_id, audio_started=True)
        self.repository.update(project_id, status="UPLOADING_REFERENCE_AUDIO", progress=5)
        client = ComfyUIClient(self._comfy_url(), self.settings.comfy_timeout_seconds)
        remote_voice = await client.upload(
            Path(project["voice_path"]), f"{project_id}_reference{Path(project['voice_path']).suffix}"
        )
        engine = project.get("tts_engine", "indextts2_legacy")
        remote_emotion_voice = None
        if engine == "indextts2_voice_clone":
            emotion_path = Path(project.get("emotion_voice_path") or "")
            if not emotion_path.is_file():
                raise ValueError("IndexTTS2 音色与情感克隆版缺少情感参考音频")
            remote_emotion_voice = await client.upload(
                emotion_path,
                f"{project_id}_emotion_reference{emotion_path.suffix}",
            )
        seed = int(project.get("seed") or random.SystemRandom().randint(1, 2**53))
        workflow = self.compiler.compile_tts(
            project["script"],
            remote_voice,
            project.get("emotion", {}),
            seed,
            engine,
            remote_emotion_voice,
        )
        engine_suffix = (
            "voice_clone" if engine == "indextts2_voice_clone" else "legacy"
        )
        workflow_path = (
            self._project_dir(project)
            / "generated"
            / f"compiled_tts_{engine_suffix}.json"
        )
        self._write_json(workflow_path, workflow)
        self.repository.update(project_id, status="GENERATING_AUDIO", progress=15, seed=seed)
        prompt_id = await client.submit(workflow)
        self.repository.update(project_id, tts_prompt_id=prompt_id)
        history = await client.wait(prompt_id)
        artifact = self._pick_artifact(
            client.artifacts(history), {".wav", ".flac", ".mp3", ".m4a", ".ogg"}
        )
        extension = Path(artifact["filename"]).suffix.lower() or ".flac"
        output = (
            self._project_dir(project)
            / "generated"
            / f"speech_{engine_suffix}{extension}"
        )
        await client.download(artifact, output)
        duration = audio_duration(output)
        self.repository.update(
            project_id,
            audio_path=str(output),
            audio_duration=round(duration, 3),
            tts_prompt_id=prompt_id,
            tts_engine=engine,
        )
        await self.align_audio(project_id)

    async def cancel_project(self, project_id: str) -> None:
        running = self.tasks.get(project_id)
        if running and not running.done():
            running.cancel()
            try:
                await running
            except asyncio.CancelledError:
                pass
        project = self.repository.get(project_id)
        if not project:
            return
        prompt_id = project.get("video_prompt_id") or project.get("tts_prompt_id")
        if prompt_id:
            try:
                await ComfyUIClient(
                    self._comfy_url(), self.settings.comfy_timeout_seconds
                ).cancel(str(prompt_id))
            except Exception:
                pass

    async def align_audio(self, project_id: str) -> None:
        project = self._project(project_id)
        if not project.get("audio_path") or not Path(project["audio_path"]).exists():
            raise ValueError("没有可用于时间对齐的音频")
        if not project.get("script"):
            raise ValueError("没有可用于时间对齐的口播稿")
        output = Path(project["audio_path"])
        duration = float(project.get("audio_duration") or audio_duration(output))
        self.repository.update(project_id, status="ALIGNING_SPEECH", progress=82)
        subtitle_max_chars = int(project.get("subtitle_max_chars", 14))
        alignment = await self.aligner.align(
            output,
            project["script"],
            duration,
            self._project_dir(project) / "asr",
            subtitle_max_chars=subtitle_max_chars,
        )
        alignment["subtitle_max_chars"] = subtitle_max_chars
        self._write_json(self._project_dir(project) / "plan" / "alignment.json", alignment)
        self.repository.update(project_id, status="PLANNING_ACTIONS", progress=90)
        segments = await self.director.plan_segments(
            project["script"],
            duration,
            project.get("image_analysis", {}),
            project.get("style", ""),
            alignment,
        )
        self._write_json(self._project_dir(project) / "plan" / "segments.json", segments)
        self.repository.update(
            project_id,
            status="PLAN_READY",
            progress=100,
            error=None,
            audio_duration=round(duration, 3),
            alignment={
                "mode": alignment["mode"],
                "confidence": alignment["confidence"],
                "note": alignment["note"],
                "recognized_text": alignment.get("recognized_text", ""),
                "asr_run_dir": alignment.get("asr_run_dir", ""),
                "sentences": alignment.get("sentences", []),
                "subtitle_cues": alignment.get("subtitle_cues", []),
                "subtitle_max_chars": alignment.get("subtitle_max_chars", 16),
                "audio_quality": alignment.get("audio_quality", {}),
            },
            segments=segments,
        )

    async def generate_video(self, project_id: str) -> None:
        project = self._project(project_id)
        if not project.get("audio_path") or not project.get("segments"):
            raise ValueError("请先生成并确认音频与动作计划")
        if project.get("bgm_enabled"):
            bgm_path = Path(project.get("bgm_path") or "")
            if not bgm_path.is_file():
                raise ValueError("已启用背景音乐，但没有可用的背景音乐文件")
        if project.get("video_prompt_id"):
            await self.resume_video(project_id)
            return
        self.repository.update(project_id, status="UPLOADING_VIDEO_ASSETS", progress=3)
        client = ComfyUIClient(self._comfy_url(), self.settings.comfy_timeout_seconds)
        image_path, audio_path = Path(project["image_path"]), Path(project["audio_path"])
        video_segments = [
            {
                **segment,
                "action_prompt": self.compiler.compact_action_prompt(segment["action_prompt"]),
            }
            for segment in project["segments"]
        ]
        if video_segments != project["segments"]:
            self.repository.update(project_id, segments=video_segments)
        remote_image, remote_audio = await asyncio.gather(
            client.upload(image_path, f"{project_id}_portrait{image_path.suffix}"),
            client.upload(audio_path, f"{project_id}_speech{audio_path.suffix}"),
        )
        workflow = self.compiler.compile_video(
            remote_image, remote_audio, video_segments, int(project["seed"])
        )
        workflow_path = self._project_dir(project) / "generated" / "compiled_video.json"
        self._write_json(workflow_path, workflow)
        prompt_id = await client.submit(workflow)
        self.repository.update(
            project_id,
            status="GENERATING_VIDEO",
            progress=10,
            error=None,
            video_prompt_id=prompt_id,
        )
        history = await client.wait(prompt_id)
        await self._save_video_result(project_id, prompt_id, history)

    async def resume_video(self, project_id: str) -> None:
        project = self._project(project_id)
        prompt_id = str(project.get("video_prompt_id") or "").strip()
        if not prompt_id:
            raise ValueError("项目没有可恢复的 ComfyUI 视频 prompt_id")
        client = ComfyUIClient(self._comfy_url(), self.settings.comfy_timeout_seconds)
        self.repository.update(
            project_id, status="GENERATING_VIDEO", progress=10, error=None
        )
        history = await client.wait(prompt_id)
        await self._save_video_result(project_id, prompt_id, history)

    async def _save_video_result(
        self, project_id: str, prompt_id: str, history: dict[str, Any]
    ) -> None:
        project = self._project(project_id)
        client = ComfyUIClient(self._comfy_url(), self.settings.comfy_timeout_seconds)
        artifact = self._pick_artifact(client.artifacts(history), {".mp4", ".webm", ".mov", ".mkv"})
        extension = Path(artifact["filename"]).suffix.lower() or ".mp4"
        output = self._project_dir(project) / "output" / f"video_raw{extension}"
        await client.download(artifact, output)
        self.repository.update(
            project_id,
            status="VIDEO_READY",
            progress=96,
            error=None,
            video_path=str(output),
            raw_video_path=str(output),
            subtitle_video_path=None,
            subtitle_path=None,
            subtitle_ass_path=None,
            bgm_video_path=None,
            video_prompt_id=prompt_id,
        )
        project = self._project(project_id)
        if project.get("subtitle_enabled") or project.get("video_title_enabled", False):
            await self.render_subtitles(project_id)
        elif project.get("bgm_enabled"):
            await self.mix_background_music(project_id)
        else:
            self.repository.update(
                project_id, status="COMPLETED", progress=100, error=None
            )

    async def render_subtitles(self, project_id: str) -> None:
        project = self._project(project_id)
        raw_video = Path(project.get("raw_video_path") or "")
        if not raw_video.is_file():
            raise ValueError("没有可用于添加字幕的原始视频")
        alignment = project.get("alignment") or {}
        cues = list(alignment.get("subtitle_cues") or [])
        max_chars = int(project.get("subtitle_max_chars", 14))
        if not cues or int(alignment.get("subtitle_max_chars", 14)) != max_chars:
            timeline = list(alignment.get("characters") or [])
            if timeline:
                cues = SpeechAlignmentService.subtitle_cues_from_timeline(
                    str(project.get("script") or project.get("original_script") or ""),
                    timeline,
                    max_chars,
                )
            else:
                cues = SubtitleDocument.cues_from_sentences(
                    list(alignment.get("sentences") or []),
                    max_chars,
                )
        if project.get("subtitle_enabled") and not cues:
            raise ValueError("没有可用的字幕时间轴，请先重新对齐音频")
        if not project.get("subtitle_enabled"):
            cues = []
        if not cues and not project.get("video_title_enabled", False):
            raise ValueError("字幕和顶部标题均未启用")

        subtitle_dir = self._project_dir(project) / "subtitle"
        subtitle_json = subtitle_dir / "subtitle.json"
        subtitle_srt = subtitle_dir / "subtitle.srt"
        subtitle_ass = subtitle_dir / "subtitle.ass"
        destination = self._project_dir(project) / "output" / "video_subtitled.mp4"
        SubtitleDocument.write_json(subtitle_json, cues)
        SubtitleDocument.write_srt(subtitle_srt, cues)
        width, height, video_duration = await self.subtitle_renderer.video_info(raw_video)
        SubtitleDocument.write_ass(
            subtitle_ass, cues, project, width=width, height=height, video_duration=video_duration
        )
        self.repository.update(
            project_id,
            status="BURNING_SUBTITLES",
            progress=97,
            error=None,
            video_path=str(raw_video),
            subtitle_video_path=None,
            bgm_video_path=None,
        )
        await self.subtitle_renderer.render(
            raw_video,
            subtitle_ass,
            destination,
            fonts_dir=self.settings.root / "resource" / "fonts",
        )
        self.repository.update(
            project_id,
            status="SUBTITLE_READY",
            progress=98,
            error=None,
            video_path=str(destination),
            subtitle_video_path=str(destination),
            subtitle_path=str(subtitle_srt),
            subtitle_ass_path=str(subtitle_ass),
            bgm_video_path=None,
        )
        project = self._project(project_id)
        if project.get("bgm_enabled"):
            await self.mix_background_music(project_id)
        else:
            self.repository.update(
                project_id, status="COMPLETED", progress=100, error=None
            )

    async def mix_background_music(self, project_id: str) -> None:
        project = self._project(project_id)
        subtitle_video = Path(project.get("subtitle_video_path") or "")
        raw_video = Path(project.get("raw_video_path") or "")
        source_video = (
            subtitle_video
            if (project.get("subtitle_enabled") or project.get("video_title_enabled", False)) and subtitle_video.is_file()
            else raw_video
        )
        speech_audio = Path(project.get("audio_path") or "")
        background_music = Path(project.get("bgm_path") or "")
        if not source_video.is_file():
            raise ValueError("没有可用于添加配乐的无配乐视频")
        if not speech_audio.is_file():
            raise ValueError("没有可用于混音的口播音频")
        if not background_music.is_file():
            raise ValueError("没有可用的背景音乐文件")

        destination = self._project_dir(project) / "output" / "video_with_bgm.mp4"
        self.repository.update(
            project_id,
            status="MIXING_BGM",
            progress=98,
            error=None,
            video_path=str(source_video),
            bgm_video_path=None,
        )
        try:
            await self.music_mixer.mix(
                source_video,
                speech_audio,
                background_music,
                destination,
                duration=float(project.get("audio_duration") or audio_duration(speech_audio)),
                volume=float(project.get("bgm_volume", 0.25)),
                ducking=bool(project.get("bgm_ducking", True)),
                fade_in=float(project.get("bgm_fade_in", 1.5)),
                fade_out=float(project.get("bgm_fade_out", 2.0)),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.repository.update(
                project_id,
                status="BGM_ERROR",
                progress=100,
                video_path=str(source_video),
                bgm_video_path=None,
                error={"type": type(exc).__name__, "message": str(exc)},
            )
            return
        self.repository.update(
            project_id,
            status="COMPLETED",
            progress=100,
            error=None,
            video_path=str(destination),
            bgm_video_path=str(destination),
        )

    def resume_interrupted_videos(self) -> int:
        resumed = 0
        for project in self.repository.list(limit=1000):
            if (
                project.get("status") != "GENERATING_VIDEO"
                or not project.get("video_prompt_id")
                or self.repository.active_tasks_for_project(project["id"])
            ):
                continue
            existing = self.tasks.get(project["id"])
            if existing and not existing.done():
                continue
            task = asyncio.create_task(self._guard(project["id"], self.resume_video))
            self.tasks[project["id"]] = task
            resumed += 1
        for project in self.repository.list(limit=1000):
            if (
                project.get("status") != "BURNING_SUBTITLES"
                or not project.get("raw_video_path")
                or self.repository.active_tasks_for_project(project["id"])
            ):
                continue
            existing = self.tasks.get(project["id"])
            if existing and not existing.done():
                continue
            task = asyncio.create_task(
                self._guard(project["id"], self.render_subtitles)
            )
            self.tasks[project["id"]] = task
            resumed += 1
        for project in self.repository.list(limit=1000):
            if (
                project.get("status") != "MIXING_BGM"
                or not project.get("raw_video_path")
                or self.repository.active_tasks_for_project(project["id"])
            ):
                continue
            existing = self.tasks.get(project["id"])
            if existing and not existing.done():
                continue
            task = asyncio.create_task(
                self._guard(project["id"], self.mix_background_music)
            )
            self.tasks[project["id"]] = task
            resumed += 1
        return resumed

    def _project(self, project_id: str) -> dict[str, Any]:
        project = self.repository.get(project_id)
        if not project:
            raise KeyError(project_id)
        return project

    @staticmethod
    def _project_dir(project: dict[str, Any]) -> Path:
        return Path(project["project_dir"])

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _pick_artifact(
        artifacts: list[dict[str, str]], extensions: set[str]
    ) -> dict[str, str]:
        matching = [a for a in artifacts if Path(a["filename"]).suffix.lower() in extensions]
        if not matching:
            raise ComfyUIError(f"任务完成但没有找到目标产物，可用产物：{artifacts}")
        saved_outputs = [artifact for artifact in matching if artifact.get("type") == "output"]
        return (saved_outputs or matching)[-1]
