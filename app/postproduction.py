from __future__ import annotations

import asyncio
import shutil
from pathlib import Path


class BackgroundMusicError(RuntimeError):
    pass


class BackgroundMusicMixer:
    def __init__(self, ffmpeg_executable: str = "ffmpeg", timeout_seconds: int = 1800):
        self.ffmpeg_executable = ffmpeg_executable
        self.timeout_seconds = timeout_seconds

    def executable(self) -> str:
        resolved = shutil.which(self.ffmpeg_executable)
        if not resolved:
            raise BackgroundMusicError(
                "找不到 FFmpeg，请安装 FFmpeg 并确保 ffmpeg 命令位于 PATH 中"
            )
        return resolved

    def command(
        self,
        raw_video: Path,
        speech_audio: Path,
        background_music: Path,
        destination: Path,
        *,
        duration: float,
        volume: float,
        ducking: bool,
        fade_in: float,
        fade_out: float,
    ) -> list[str]:
        duration = max(0.1, float(duration))
        volume = min(1.0, max(0.0, float(volume)))
        fade_in = min(duration, max(0.0, float(fade_in)))
        fade_out = min(duration, max(0.0, float(fade_out)))
        fade_out_start = max(0.0, duration - fade_out)

        music_filters = [
            "aformat=sample_rates=48000:channel_layouts=stereo",
            "loudnorm=I=-20:TP=-2:LRA=7",
            f"volume={volume:.4f}",
        ]
        if fade_in:
            music_filters.append(f"afade=t=in:st=0:d={fade_in:.3f}")
        if fade_out:
            music_filters.append(
                f"afade=t=out:st={fade_out_start:.3f}:d={fade_out:.3f}"
            )

        filters = [f"[2:a]{','.join(music_filters)}[music]"]
        if ducking:
            filters.extend(
                [
                    "[1:a]aformat=sample_rates=48000:channel_layouts=stereo,"
                    "asplit=2[speech_mix][speech_side]",
                    "[music][speech_side]sidechaincompress="
                    "threshold=0.025:ratio=8:attack=80:release=500[ducked]",
                    "[speech_mix][ducked]amix=inputs=2:duration=first:"
                    "dropout_transition=2,alimiter=limit=0.891[outa]",
                ]
            )
        else:
            filters.extend(
                [
                    "[1:a]aformat=sample_rates=48000:channel_layouts=stereo[speech]",
                    "[speech][music]amix=inputs=2:duration=first:"
                    "dropout_transition=2,alimiter=limit=0.891[outa]",
                ]
            )

        return [
            self.executable(),
            "-y",
            "-i",
            str(raw_video),
            "-i",
            str(speech_audio),
            "-stream_loop",
            "-1",
            "-i",
            str(background_music),
            "-filter_complex",
            ";".join(filters),
            "-map",
            "0:v:0",
            "-map",
            "[outa]",
            "-t",
            f"{duration:.3f}",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(destination),
        ]

    async def mix(
        self,
        raw_video: Path,
        speech_audio: Path,
        background_music: Path,
        destination: Path,
        *,
        duration: float,
        volume: float = 0.25,
        ducking: bool = True,
        fade_in: float = 1.5,
        fade_out: float = 2.0,
    ) -> Path:
        for path, label in (
            (raw_video, "无配乐视频"),
            (speech_audio, "口播音频"),
            (background_music, "背景音乐"),
        ):
            if not path.is_file():
                raise BackgroundMusicError(f"{label}不存在：{path}")

        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(
            f"{destination.stem}.tmp{destination.suffix or '.mp4'}"
        )
        if temporary.exists():
            temporary.unlink()
        command = self.command(
            raw_video,
            speech_audio,
            background_music,
            temporary,
            duration=duration,
            volume=volume,
            ducking=ducking,
            fade_in=fade_in,
            fade_out=fade_out,
        )
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.timeout_seconds
            )
        except asyncio.CancelledError:
            process.terminate()
            await process.wait()
            raise
        except asyncio.TimeoutError as exc:
            process.terminate()
            await process.wait()
            raise BackgroundMusicError(
                f"FFmpeg 配乐处理超过 {self.timeout_seconds} 秒"
            ) from exc
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise BackgroundMusicError(f"FFmpeg 配乐处理失败：\n{detail}")
        temporary.replace(destination)
        return destination
