from __future__ import annotations

import asyncio
import difflib
import json
import math
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings
from .subtitle_segmentation import (
    SUBTITLE_SEGMENTATION_VERSION,
    balanced_chunk_ranges,
)


@dataclass
class TimedChar:
    char: str
    start: float
    end: float


class SpeechAlignmentService:
    """Aligns the known TTS script to the generated audio, then projects it onto 4s video windows."""

    def __init__(self, settings: Settings):
        self.settings = settings

    async def align(
        self,
        audio_path: Path,
        script: str,
        duration: float,
        work_dir: Path | None = None,
        subtitle_max_chars: int = 14,
    ) -> dict[str, Any]:
        if not self.settings.asr_enabled:
            chars = self._estimated_chars(script, duration)
            return self._build_result(
                script,
                duration,
                chars,
                "estimated",
                0.45,
                f"未找到本地 Whisper 命令：{self.settings.whisper_executable}，按字符权重估算",
                subtitle_max_chars,
            )
        try:
            transcription = await self._transcribe(audio_path, work_dir or audio_path.parent / "asr")
            asr_chars = self._asr_chars(transcription)
            if not asr_chars:
                raise ValueError("ASR 未返回 word 或 segment 时间戳")
            source_chars, confidence = self._force_align(script, asr_chars, duration)
            if confidence < 0.28:
                raise ValueError(f"ASR 与原稿匹配度过低（{confidence:.0%}）")
            result = self._build_result(
                script, duration, source_chars, "asr_forced", confidence, "ASR 时间戳与原始口播稿字符级对齐", subtitle_max_chars
            )
            result["recognized_text"] = str(transcription.get("text", ""))
            result["asr_run_dir"] = str(transcription.get("_local_run_dir", ""))
            return result
        except Exception as exc:
            chars = self._estimated_chars(script, duration)
            return self._build_result(
                script, duration, chars, "estimated_fallback", 0.35, f"ASR 对齐失败，已降级估算：{exc}", subtitle_max_chars
            )

    async def _transcribe(self, audio_path: Path, work_dir: Path) -> dict[str, Any]:
        if not audio_path.is_file():
            raise FileNotFoundError(f"Whisper 输入音频不存在：{audio_path}")
        executable = self.settings.whisper_path
        if not executable:
            raise FileNotFoundError(f"找不到 Whisper 命令：{self.settings.whisper_executable}")
        run_dir = work_dir / uuid.uuid4().hex[:10]
        run_dir.mkdir(parents=True, exist_ok=False)
        command = self._command(executable, audio_path, run_dir)
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.settings.whisper_timeout_seconds
            )
        except asyncio.TimeoutError as exc:
            process.kill()
            stdout, stderr = await process.communicate()
            self._write_log(run_dir, command, stdout, stderr, "TIMEOUT")
            raise TimeoutError(
                f"Whisper 超过 {self.settings.whisper_timeout_seconds} 秒未完成"
            ) from exc
        self._write_log(run_dir, command, stdout, stderr, str(process.returncode))
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace")[-4000:]
            raise RuntimeError(f"Whisper 执行失败（exit={process.returncode}）：{detail}")
        expected = run_dir / f"{audio_path.stem}.json"
        candidates = [expected] if expected.is_file() else sorted(run_dir.glob("*.json"))
        if not candidates:
            raise RuntimeError(f"Whisper 执行成功但没有生成 JSON：{run_dir}")
        payload = json.loads(candidates[-1].read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Whisper JSON 格式无效")
        payload["_local_run_dir"] = str(run_dir)
        return payload

    def _command(self, executable: str, audio_path: Path, output_dir: Path) -> list[str]:
        command = [
            executable,
            str(audio_path),
            "--model",
            self.settings.whisper_model,
            "--language",
            self.settings.whisper_language,
            "--output_format",
            "json",
            "--word_timestamps",
            "True" if self.settings.whisper_word_timestamps else "False",
            "--output_dir",
            str(output_dir),
            "--verbose",
            "False",
        ]
        if self.settings.whisper_model_dir:
            command.extend(["--model_dir", self.settings.whisper_model_dir])
        if self.settings.whisper_device:
            command.extend(["--device", self.settings.whisper_device])
        return command

    @staticmethod
    def _write_log(
        run_dir: Path, command: list[str], stdout: bytes, stderr: bytes, result: str
    ) -> None:
        log = [
            "command=" + json.dumps(command, ensure_ascii=False),
            f"result={result}",
            "\n[stdout]\n" + stdout.decode("utf-8", errors="replace"),
            "\n[stderr]\n" + stderr.decode("utf-8", errors="replace"),
        ]
        (run_dir / "whisper.log").write_text("\n".join(log), encoding="utf-8")

    @classmethod
    def _asr_chars(cls, payload: dict[str, Any]) -> list[TimedChar]:
        units = payload.get("words") or []
        if not units:
            units = [
                word
                for segment in payload.get("segments", [])
                if isinstance(segment, dict)
                for word in segment.get("words", [])
                if isinstance(word, dict)
            ]
        if not units:
            units = payload.get("segments") or []
        result: list[TimedChar] = []
        for unit in units:
            if not isinstance(unit, dict):
                continue
            text = str(unit.get("word") or unit.get("text") or "")
            normalized = cls._normalized(text)
            if not normalized:
                continue
            start, end = float(unit.get("start", 0)), float(unit.get("end", 0))
            if end <= start:
                end = start + 0.04 * len(normalized)
            span = (end - start) / len(normalized)
            for index, char in enumerate(normalized):
                result.append(TimedChar(char, start + index * span, start + (index + 1) * span))
        return result

    @classmethod
    def _force_align(
        cls, script: str, recognized: list[TimedChar], duration: float
    ) -> tuple[list[TimedChar], float]:
        source = cls._normalized(script)
        target = "".join(item.char for item in recognized)
        matcher = difflib.SequenceMatcher(None, source, target, autojunk=False)
        anchors: dict[int, tuple[float, float]] = {}
        matched = 0
        for block in matcher.get_matching_blocks():
            for offset in range(block.size):
                timed = recognized[block.b + offset]
                anchors[block.a + offset] = (timed.start, timed.end)
                matched += 1
        confidence = matched / max(1, len(source))
        normalized_times: list[tuple[float, float] | None] = [anchors.get(i) for i in range(len(source))]
        known = sorted(anchors)
        if not known:
            return cls._estimated_chars(script, duration), 0.0

        for index in range(len(source)):
            if normalized_times[index] is not None:
                continue
            left = next((k for k in reversed(known) if k < index), None)
            right = next((k for k in known if k > index), None)
            left_time = anchors[left][1] if left is not None else 0.0
            right_time = anchors[right][0] if right is not None else duration
            left_index = left if left is not None else -1
            right_index = right if right is not None else len(source)
            slots = max(1, right_index - left_index - 1)
            position = index - left_index - 1
            span = max(0.02, (right_time - left_time) / slots)
            start = left_time + position * span
            normalized_times[index] = (start, min(duration, start + span))

        result: list[TimedChar] = []
        norm_index = 0
        for char in script:
            normalized = cls._normalized(char)
            if normalized:
                start, end = normalized_times[norm_index] or (0.0, 0.0)
                result.append(TimedChar(char, start, end))
                norm_index += 1
            else:
                previous = result[-1].end if result else 0.0
                next_time = normalized_times[norm_index][0] if norm_index < len(normalized_times) and normalized_times[norm_index] else previous
                point = min(duration, max(previous, next_time))
                result.append(TimedChar(char, point, point))
        return result, confidence

    @classmethod
    def _estimated_chars(cls, script: str, duration: float) -> list[TimedChar]:
        weights = [cls._weight(char) for char in script]
        total = sum(weights) or 1.0
        cursor = 0.0
        result = []
        for char, weight in zip(script, weights):
            start = duration * cursor / total
            cursor += weight
            end = duration * cursor / total
            result.append(TimedChar(char, start, end))
        return result

    @classmethod
    def _build_result(
        cls,
        script: str,
        duration: float,
        chars: list[TimedChar],
        mode: str,
        confidence: float,
        note: str,
        subtitle_max_chars: int = 14,
    ) -> dict[str, Any]:
        count = max(1, math.ceil((math.ceil(duration * 25) - 9) / 100))
        sentence_ranges = cls._sentence_ranges(script)
        sentences = cls._sentence_timeline(script, chars, sentence_ranges)
        subtitle_cues = cls._subtitle_cues(
            script, chars, sentence_ranges, subtitle_max_chars
        )
        audio_quality = cls._diagnose_audio_timing(
            sentences, chars, mode, confidence, duration
        )
        windows: list[dict[str, Any]] = []
        for index in range(count):
            start, end = index * 4.0, min(duration, (index + 1) * 4.0)
            selected = [i for i, item in enumerate(chars) if cls._inside(item, start, end, index == count - 1)]
            spoken = cls._slice_text(script, selected)
            events: list[dict[str, Any]] = []
            contexts: list[str] = []
            for sentence_start, sentence_end in sentence_ranges:
                timed = [item for item in chars[sentence_start:sentence_end] if cls._normalized(item.char)]
                if not timed:
                    continue
                event_start, event_end = timed[0].start, timed[-1].end
                if event_end <= start or event_start >= end:
                    continue
                local_indices = [
                    pos for pos in range(sentence_start, sentence_end)
                    if cls._inside(chars[pos], start, end, index == count - 1)
                ]
                full_sentence = script[sentence_start:sentence_end].strip()
                local_text = cls._slice_text(script, local_indices).strip()
                if full_sentence and full_sentence not in contexts:
                    contexts.append(full_sentence)
                events.append(
                    {
                        "text": local_text,
                        "full_sentence": full_sentence,
                        "absolute_start": round(event_start, 3),
                        "absolute_end": round(event_end, 3),
                        "local_start": round(max(0.0, event_start - start), 3),
                        "local_end": round(min(end - start, event_end - start), 3),
                        "enters_before": event_start < start - 0.02,
                        "continues_after": event_end > end + 0.02,
                    }
                )
            windows.append(
                {
                    "index": index,
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "spoken_text": spoken.strip() or "自然停顿",
                    "sentence_context": " / ".join(contexts),
                    "starts_mid_sentence": bool(events and events[0]["enters_before"]),
                    "ends_mid_sentence": bool(events and events[-1]["continues_after"]),
                    "speech_events": events,
                }
            )
        return {
            "mode": mode,
            "confidence": round(confidence, 4),
            "note": note,
            "duration": round(duration, 3),
            "sentences": sentences,
            "characters": [
                {
                    "char": item.char,
                    "start": round(item.start, 3),
                    "end": round(item.end, 3),
                }
                for item in chars
            ],
            "subtitle_cues": subtitle_cues,
            "subtitle_segmentation_version": SUBTITLE_SEGMENTATION_VERSION,
            "audio_quality": audio_quality,
            "windows": windows,
        }

    @classmethod
    def subtitle_cues_from_timeline(
        cls, script: str, timeline: list[dict[str, Any]], max_chars: int = 14
    ) -> list[dict[str, Any]]:
        chars = [
            TimedChar(
                str(item.get("char") or ""),
                float(item.get("start") or 0),
                float(item.get("end") or 0),
            )
            for item in timeline
        ]
        return cls._subtitle_cues(script, chars, cls._sentence_ranges(script), max_chars)

    @classmethod
    def _subtitle_cues(
        cls,
        script: str,
        chars: list[TimedChar],
        sentence_ranges: list[tuple[int, int]],
        max_chars: int = 16,
    ) -> list[dict[str, Any]]:
        """Build readable cues from the original script and its forced timestamps."""
        cues: list[dict[str, Any]] = []

        def append_cue(items: list[TimedChar]) -> None:
            text = "".join(item.char for item in items).strip()
            timed = [item for item in items if cls._normalized(item.char)]
            if text and not timed and cues:
                cues[-1]["text"] += text
                return
            if not text or not timed:
                return
            cues.append(
                {
                    "index": len(cues) + 1,
                    "start": round(timed[0].start, 3),
                    "end": round(max(timed[0].start + 0.05, timed[-1].end), 3),
                    "text": text,
                }
            )

        for sentence_start, sentence_end in sentence_ranges:
            sentence_chars = chars[sentence_start:sentence_end]
            sentence_text = "".join(item.char for item in sentence_chars)
            for chunk_start, chunk_end in balanced_chunk_ranges(sentence_text, max_chars):
                append_cue(sentence_chars[chunk_start:chunk_end])

        for index, cue in enumerate(cues):
            next_start = (
                float(cues[index + 1]["start"])
                if index + 1 < len(cues)
                else float(cue["end"])
            )
            cue["end"] = round(
                max(float(cue["end"]), min(next_start, float(cue["start"]) + 0.45)),
                3,
            )
            if index and cue["start"] < cues[index - 1]["end"]:
                cues[index - 1]["end"] = round(float(cue["start"]), 3)
        return cues

    @classmethod
    def _sentence_timeline(
        cls,
        script: str,
        chars: list[TimedChar],
        sentence_ranges: list[tuple[int, int]],
    ) -> list[dict[str, Any]]:
        sentences: list[dict[str, Any]] = []
        for index, (sentence_start, sentence_end) in enumerate(sentence_ranges):
            timed = [
                item
                for item in chars[sentence_start:sentence_end]
                if cls._normalized(item.char)
            ]
            if not timed:
                continue
            start, end = timed[0].start, timed[-1].end
            internal_pauses = []
            for left, right in zip(timed, timed[1:]):
                gap = max(0.0, right.start - left.end)
                if gap >= 0.65:
                    internal_pauses.append(
                        {
                            "start": round(left.end, 3),
                            "end": round(right.start, 3),
                            "duration": round(gap, 3),
                        }
                    )
            normalized_length = len(cls._normalized(script[sentence_start:sentence_end]))
            speech_duration = max(0.001, end - start)
            sentences.append(
                {
                    "index": index,
                    "text": script[sentence_start:sentence_end].strip(),
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "duration": round(speech_duration, 3),
                    "chars_per_second": round(normalized_length / speech_duration, 2),
                    "pause_before": 0.0,
                    "pause_after": 0.0,
                    "internal_pauses": internal_pauses,
                }
            )
        for index, sentence in enumerate(sentences):
            if index:
                sentence["pause_before"] = round(
                    max(0.0, sentence["start"] - sentences[index - 1]["end"]), 3
                )
            if index + 1 < len(sentences):
                sentence["pause_after"] = round(
                    max(0.0, sentences[index + 1]["start"] - sentence["end"]), 3
                )
        return sentences

    @classmethod
    def _diagnose_audio_timing(
        cls,
        sentences: list[dict[str, Any]],
        chars: list[TimedChar],
        mode: str,
        confidence: float,
        duration: float,
    ) -> dict[str, Any]:
        if not mode.startswith("asr"):
            return {
                "status": "unavailable",
                "has_suspected_interruption": False,
                "summary": "本地 Whisper 未成功运行，当前只有估算时间，不能可靠判断音频中断原因。",
                "issues": [],
            }

        issues: list[dict[str, Any]] = []
        for sentence in sentences:
            for pause in sentence["internal_pauses"]:
                severity = "high" if pause["duration"] >= 1.5 else "medium"
                issues.append(
                    {
                        "type": "long_pause_inside_sentence",
                        "severity": severity,
                        "sentence_index": sentence["index"],
                        "time": pause["start"],
                        "duration": pause["duration"],
                        "reason": "同一句内部出现长静音，可能是 IndexTTS2 合成停顿、文本换行或模型断句造成。",
                    }
                )
            if sentence["pause_after"] >= 1.5:
                issues.append(
                    {
                        "type": "long_pause_between_sentences",
                        "severity": "medium",
                        "sentence_index": sentence["index"],
                        "time": sentence["end"],
                        "duration": sentence["pause_after"],
                        "reason": "句子边界停顿明显偏长，通常由标点、换行或 TTS 自动分句引起。",
                    }
                )
            speed = sentence["chars_per_second"]
            if speed < 1.4 or speed > 8.0:
                issues.append(
                    {
                        "type": "abnormal_speech_rate",
                        "severity": "medium",
                        "sentence_index": sentence["index"],
                        "time": sentence["start"],
                        "duration": sentence["duration"],
                        "reason": f"该句语速约 {speed} 字/秒，可能存在拖音、吞字或异常加速。",
                    }
                )
        if confidence < 0.75:
            issues.append(
                {
                    "type": "script_recognition_mismatch",
                    "severity": "high" if confidence < 0.5 else "medium",
                    "sentence_index": None,
                    "time": None,
                    "duration": None,
                    "reason": "Whisper 识别内容与原稿匹配度偏低，可能存在漏读、错读、重复或局部音频异常。",
                }
            )

        spoken = [item for item in chars if cls._normalized(item.char)]
        if spoken:
            leading_silence = max(0.0, spoken[0].start)
            trailing_silence = max(0.0, duration - spoken[-1].end)
        else:
            leading_silence, trailing_silence = duration, 0.0
        suspected = any(
            issue["type"]
            in {
                "long_pause_inside_sentence",
                "script_recognition_mismatch",
                "abnormal_speech_rate",
            }
            for issue in issues
        )
        if suspected:
            summary = "检测到疑似中断或口播异常，请按 issues 中的时间点试听确认。"
            status = "warning"
        elif issues:
            summary = "未检测到句内中断，但存在较长句间停顿。"
            status = "review"
        else:
            summary = "未从 Whisper 时间轴检测到明显中断。"
            status = "passed"
        return {
            "status": status,
            "has_suspected_interruption": suspected,
            "summary": summary,
            "leading_silence": round(leading_silence, 3),
            "trailing_silence": round(trailing_silence, 3),
            "issues": issues,
            "limitation": "Whisper 能定位静音、漏读和语速异常；瞬时爆音或无静音的音色跳变仍需波形/人工试听确认。",
        }

    @staticmethod
    def _inside(item: TimedChar, start: float, end: float, last: bool) -> bool:
        point = (item.start + item.end) / 2
        return start <= point <= end if last else start <= point < end

    @staticmethod
    def _slice_text(script: str, indices: list[int]) -> str:
        if not indices:
            return ""
        return script[min(indices) : max(indices) + 1]

    @staticmethod
    def _sentence_ranges(script: str) -> list[tuple[int, int]]:
        ranges, start = [], 0
        for match in re.finditer(r"[。！？!?；;\n]+", script):
            end = match.end()
            if script[start:end].strip():
                ranges.append((start, end))
            start = end
        if script[start:].strip():
            ranges.append((start, len(script)))
        return ranges or [(0, len(script))]

    @staticmethod
    def _normalized(value: str) -> str:
        return "".join(char.lower() for char in value if char.isalnum() or "\u4e00" <= char <= "\u9fff")

    @staticmethod
    def _weight(char: str) -> float:
        if "\u4e00" <= char <= "\u9fff":
            return 1.0
        if char.isalnum():
            return 0.45
        if char in "，,":
            return 0.35
        if char in "。！？!?；;":
            return 0.65
        return 0.1
