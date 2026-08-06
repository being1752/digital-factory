from __future__ import annotations

import struct
import wave
from pathlib import Path


class UnsupportedAudioError(ValueError):
    pass


def audio_duration(path: Path) -> float:
    suffix = path.suffix.lower()
    if suffix in {".wav", ".wave"}:
        with wave.open(str(path), "rb") as stream:
            return stream.getnframes() / float(stream.getframerate())
    if suffix == ".flac":
        return _flac_duration(path)
    if suffix in {".mp4", ".m4a", ".m4s", ".mov"}:
        return _mp4_duration(path)
    try:
        from mutagen import File  # type: ignore

        media = File(path)
        if media and getattr(media, "info", None) and getattr(media.info, "length", None):
            return float(media.info.length)
    except ImportError:
        pass
    raise UnsupportedAudioError(f"无法读取音频时长：{path.name}")


def _flac_duration(path: Path) -> float:
    with path.open("rb") as stream:
        if stream.read(4) != b"fLaC":
            raise UnsupportedAudioError("FLAC 文件头无效")
        while True:
            header = stream.read(4)
            if len(header) != 4:
                break
            last = bool(header[0] & 0x80)
            block_type = header[0] & 0x7F
            length = int.from_bytes(header[1:4], "big")
            block = stream.read(length)
            if block_type == 0 and len(block) >= 18:
                packed = int.from_bytes(block[10:18], "big")
                sample_rate = (packed >> 44) & 0xFFFFF
                total_samples = packed & 0xFFFFFFFFF
                if sample_rate:
                    return total_samples / sample_rate
            if last:
                break
    raise UnsupportedAudioError("FLAC 中没有有效的 STREAMINFO")


def _mp4_duration(path: Path) -> float:
    data = path.read_bytes()
    marker = data.find(b"mvhd")
    if marker < 0 or marker + 24 > len(data):
        raise UnsupportedAudioError("MP4/M4A 中没有 mvhd")
    version = data[marker + 4]
    if version == 0:
        timescale = struct.unpack(">I", data[marker + 16 : marker + 20])[0]
        duration = struct.unpack(">I", data[marker + 20 : marker + 24])[0]
    elif version == 1:
        timescale = struct.unpack(">I", data[marker + 24 : marker + 28])[0]
        duration = struct.unpack(">Q", data[marker + 28 : marker + 36])[0]
    else:
        raise UnsupportedAudioError(f"未知 mvhd 版本：{version}")
    if not timescale:
        raise UnsupportedAudioError("MP4 timescale 为 0")
    return duration / timescale
