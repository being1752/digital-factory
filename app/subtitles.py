from __future__ import annotations

import asyncio
import os
import json
import shutil
import re
from pathlib import Path
from typing import Any

from .subtitle_segmentation import balanced_text_chunks


class SubtitleError(RuntimeError):
    pass


def _timestamp_srt(seconds: float) -> str:
    milliseconds = max(0, round(float(seconds) * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _timestamp_ass(seconds: float) -> str:
    centiseconds = max(0, round(float(seconds) * 100))
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6000)
    secs, centis = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def _ass_color(value: str, opacity: int = 100) -> str:
    value = str(value or "#FFFFFF").strip()
    if len(value) != 7 or not value.startswith("#"):
        value = "#FFFFFF"
    try:
        red, green, blue = int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)
    except ValueError:
        red, green, blue = 255, 255, 255
    alpha = round(255 * (1 - min(100, max(0, int(opacity))) / 100))
    return f"&H{alpha:02X}{blue:02X}{green:02X}{red:02X}"


def subtitle_display_text(value: str) -> str:
    """Create clean short-video subtitle text without changing alignment text."""
    source = str(value or "").strip()
    output: list[str] = []
    hidden = "，、。；：,.;:"
    for index, char in enumerate(source):
        if char not in hidden:
            output.append(char)
            continue
        previous = source[index - 1] if index else ""
        following = source[index + 1] if index + 1 < len(source) else ""
        # Preserve numeric punctuation such as 3.5, 1,000 and 12:30.
        if previous.isdigit() and following.isdigit() and char in ".,:":
            output.append(char)
        else:
            output.append(" ")
    return re.sub(r"\s+", " ", "".join(output)).strip()


def _title_lines(value: str, max_chars: int = 12) -> list[str]:
    source = str(value or "").replace("\r\n", "\n").strip()
    chunks: list[str] = []
    for raw_line in source.split("\n"):
        line = "".join(raw_line.split()).strip()
        while line and len(chunks) < 3:
            chunks.append(line[:max_chars])
            line = line[max_chars:]
        if len(chunks) >= 3:
            break
    return chunks


def _ass_text(value: str) -> str:
    return (
        str(value or "")
        .replace("{", "｛")
        .replace("}", "｝")
        .replace("\r\n", "\\N")
        .replace("\n", "\\N")
        .strip()
    )


class SubtitleDocument:
    @staticmethod
    def cues_from_sentences(
        sentences: list[dict[str, Any]], max_chars: int = 14
    ) -> list[dict[str, Any]]:
        """Split legacy sentence timelines when character-level cues are unavailable."""
        max_chars = min(32, max(6, int(max_chars)))
        cues: list[dict[str, Any]] = []
        for sentence in sentences:
            text = str(sentence.get("text") or "").strip()
            start, end = float(sentence.get("start") or 0), float(sentence.get("end") or 0)
            if not text or end <= start:
                continue
            chunks = [chunk.strip() for chunk in balanced_text_chunks(text, max_chars) if chunk.strip()]
            weights = [max(1, len("".join(chunk.split()))) for chunk in chunks]
            total = sum(weights) or 1
            cursor = start
            for index, (chunk, weight) in enumerate(zip(chunks, weights)):
                chunk_end = (
                    end
                    if index == len(chunks) - 1
                    else cursor + (end - start) * weight / total
                )
                cues.append(
                    {
                        "index": len(cues) + 1,
                        "start": round(cursor, 3),
                        "end": round(max(cursor + 0.05, chunk_end), 3),
                        "text": chunk,
                    }
                )
                cursor = chunk_end
        return cues

    @staticmethod
    def write_srt(path: Path, cues: list[dict[str, Any]]) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        blocks = []
        for index, cue in enumerate(cues, 1):
            blocks.append(
                f"{index}\n{_timestamp_srt(cue['start'])} --> {_timestamp_srt(cue['end'])}\n"
                f"{subtitle_display_text(cue['text'])}"
            )
        path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
        return path

    @staticmethod
    def write_json(path: Path, cues: list[dict[str, Any]]) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        display_cues = [{**cue, "text": subtitle_display_text(cue.get("text", ""))} for cue in cues]
        path.write_text(json.dumps(display_cues, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    @staticmethod
    def write_ass(
        path: Path,
        cues: list[dict[str, Any]],
        settings: dict[str, Any],
        width: int,
        height: int,
        video_duration: float | None = None,
    ) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        width, height = max(16, int(width)), max(16, int(height))
        font_size = max(12, round(float(settings.get("subtitle_font_size", 60)) * width / 1080))
        font_name = str(settings.get("subtitle_font_name") or "Microsoft YaHei").replace(",", " ")
        primary = _ass_color(str(settings.get("subtitle_font_color") or "#FFFFFF"))
        stroke = _ass_color(str(settings.get("subtitle_stroke_color") or "#000000"))
        stroke_width = max(0, round(float(settings.get("subtitle_stroke_width", 3)) * width / 1080, 2))
        bold = -1 if bool(settings.get("subtitle_font_bold", True)) else 0
        background = _ass_color(
            str(settings.get("subtitle_background_color") or "#000000"),
            int(settings.get("subtitle_background_opacity", 55)),
        )
        position = str(settings.get("subtitle_position") or "bottom")
        alignment = {"top": 8, "center": 5, "bottom": 2, "custom": 5}.get(position, 2)
        margin_v = max(10, round(height * (0.05 if position == "top" else 0.08)))
        background_enabled = bool(settings.get("subtitle_background_enabled", False))
        title_lines = _title_lines(str(settings.get("video_title") or ""))
        title_enabled = bool(settings.get("video_title_enabled", True)) and bool(title_lines)
        title_font = str(settings.get("video_title_font_name") or "Microsoft YaHei").replace(",", " ")
        title_size = max(18, round(float(settings.get("video_title_font_size", 88)) * width / 1080))
        title_primary = _ass_color(str(settings.get("video_title_primary_color") or "#FFFFFF"))
        title_stroke = _ass_color(str(settings.get("video_title_stroke_color") or "#000000"))
        title_outline = max(0, round(float(settings.get("video_title_stroke_width", 4)) * width / 1080, 2))
        title_y = round(height * min(50, max(0, float(settings.get("video_title_position", 10)))) / 100)
        format_line = (
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
            "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
            "MarginR, MarginV, Encoding"
        )
        styles = [
            "Style: Text,{font},{size},{primary},{primary},{stroke},&H00000000,{bold},0,0,0,"
            "100,100,0,0,1,{outline},0,{align},20,20,{margin},1".format(
                font=font_name,
                size=font_size,
                primary=primary,
                stroke=stroke,
                outline=stroke_width,
                bold=bold,
                align=alignment,
                margin=margin_v,
            )
        ]
        if title_enabled:
            styles.append(
                "Style: Title,{font},{size},{primary},{primary},{stroke},&H00000000,-1,0,0,0,100,100,0,0,1,{outline},0,8,20,20,10,1".format(font=title_font,size=title_size,primary=title_primary,stroke=title_stroke,outline=title_outline)
            )
        if background_enabled:
            padding = max(4, round(font_size * 0.28))
            styles.append(
                "Style: Box,{font},{size},&HFF000000,&HFF000000,{background},&HFF000000,"
                "0,0,0,0,100,100,0,0,3,{padding},0,{align},20,20,{margin},1".format(
                    font=font_name,
                    size=font_size,
                    background=background,
                    padding=padding,
                    align=alignment,
                    margin=margin_v,
                )
            )
        lines = [
            "[Script Info]",
            "ScriptType: v4.00+",
            "WrapStyle: 0",
            "ScaledBorderAndShadow: yes",
            f"PlayResX: {width}",
            f"PlayResY: {height}",
            "YCbCr Matrix: TV.709",
            "",
            "[V4+ Styles]",
            format_line,
            *styles,
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
        if title_enabled:
            duration = max(float(video_duration or 0), max((float(cue.get("end") or 0) for cue in cues), default=0), 0.05)
            start, end = _timestamp_ass(0), _timestamp_ass(duration)
            line_gap = max(1, round(title_size * 1.2))
            for index, title_line in enumerate(title_lines):
                text = f"{{\\pos({width // 2},{title_y + index * line_gap})}}{_ass_text(title_line)}"
                lines.append(f"Dialogue: 2,{start},{end},Title,,0,0,0,,{text}")
        custom_y = round(height * min(100, max(0, float(settings.get("subtitle_custom_position", 78)))) / 100)
        for cue in cues:
            text = _ass_text(subtitle_display_text(cue.get("text", "")))
            if position == "custom":
                text = f"{{\\pos({width // 2},{custom_y})}}{text}"
            start, end = _timestamp_ass(cue["start"]), _timestamp_ass(cue["end"])
            if background_enabled:
                lines.append(f"Dialogue: 0,{start},{end},Box,,0,0,0,,{text}")
            lines.append(f"Dialogue: 1,{start},{end},Text,,0,0,0,,{text}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
        return path


class SubtitleRenderer:
    def __init__(
        self,
        ffmpeg_executable: str = "ffmpeg",
        ffprobe_executable: str = "ffprobe",
        timeout_seconds: int = 1800,
    ):
        self.ffmpeg_executable = ffmpeg_executable
        self.ffprobe_executable = ffprobe_executable
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _executable(value: str) -> str:
        resolved = shutil.which(value)
        if not resolved:
            raise SubtitleError(f"找不到媒体处理命令：{value}")
        return resolved

    async def video_info(self, video: Path) -> tuple[int, int, float]:
        process = await asyncio.create_subprocess_exec(
            self._executable(self.ffprobe_executable),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height:format=duration",
            "-of",
            "json",
            str(video),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise SubtitleError(stderr.decode("utf-8", errors="replace").strip())
        payload = json.loads(stdout.decode("utf-8"))
        streams = payload.get("streams") or []
        if not streams:
            raise SubtitleError("视频中没有可用画面流")
        duration = float((payload.get("format") or {}).get("duration") or 0)
        return int(streams[0]["width"]), int(streams[0]["height"]), duration

    async def video_size(self, video: Path) -> tuple[int, int]:
        width, height, _ = await self.video_info(video)
        return width, height

    async def render(
        self,
        video: Path,
        ass_file: Path,
        destination: Path,
        fonts_dir: Path | None = None,
    ) -> Path:
        if not video.is_file():
            raise SubtitleError(f"原始视频不存在：{video}")
        if not ass_file.is_file():
            raise SubtitleError(f"字幕文件不存在：{ass_file}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f"{destination.stem}.tmp{destination.suffix}")
        if temporary.exists():
            temporary.unlink()
        subtitle_filter = f"ass={ass_file.name}"
        if fonts_dir and fonts_dir.is_dir():
            relative_fonts = Path(os.path.relpath(fonts_dir, ass_file.parent)).as_posix()
            relative_fonts = relative_fonts.replace("'", "\\'")
            subtitle_filter += f":fontsdir='{relative_fonts}'"
        process = await asyncio.create_subprocess_exec(
            self._executable(self.ffmpeg_executable),
            "-y",
            "-i",
            str(video),
            "-vf",
            subtitle_filter,
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(temporary),
            cwd=str(ass_file.parent),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.timeout_seconds
            )
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.wait()
            raise SubtitleError(f"字幕渲染超过 {self.timeout_seconds} 秒") from exc
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise SubtitleError(f"FFmpeg字幕渲染失败：\n{detail}")
        temporary.replace(destination)
        return destination
