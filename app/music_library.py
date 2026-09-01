from __future__ import annotations

import secrets
import shutil
from pathlib import Path
from typing import Callable, Sequence


SUPPORTED_MUSIC_EXTENSIONS = {
    ".wav",
    ".flac",
    ".mp3",
    ".m4a",
    ".m4s",
    ".mp4",
    ".ogg",
    ".aac",
}


class MusicLibraryError(RuntimeError):
    pass


def available_music_files(library: Path) -> list[Path]:
    if not library.is_dir():
        return []
    return sorted(
        (
            path
            for path in library.iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_MUSIC_EXTENSIONS
        ),
        key=lambda path: path.name.casefold(),
    )


def copy_random_music(
    library: Path,
    input_dir: Path,
    chooser: Callable[[Sequence[Path]], Path] = secrets.choice,
) -> tuple[Path, str]:
    candidates = available_music_files(library)
    if not candidates:
        raise MusicLibraryError("指定的音乐文件夹不存在或没有支持的音乐文件")
    source = chooser(candidates)
    destination = input_dir / f"background_music_library{source.suffix.lower()}"
    input_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return destination, source.name


def music_file_by_name(library: Path, name: str) -> Path:
    """Return a library file by exact basename without allowing path traversal."""
    candidate_name = Path(str(name or "")).name
    if not candidate_name or candidate_name != str(name or ""):
        raise MusicLibraryError("选择的背景音乐无效")
    for candidate in available_music_files(library):
        if candidate.name == candidate_name:
            return candidate
    raise MusicLibraryError("选择的背景音乐不存在或已被移除")


def copy_music_by_name(library: Path, input_dir: Path, name: str) -> tuple[Path, str]:
    source = music_file_by_name(library, name)
    destination = input_dir / f"background_music_library{source.suffix.lower()}"
    input_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return destination, source.name
