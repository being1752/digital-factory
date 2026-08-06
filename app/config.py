from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_COMFY_URL = "http://127.0.0.1:8188"


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    root: Path = ROOT
    data_dir: Path = Path(os.getenv("DATA_DIR", str(ROOT / "data"))).resolve()
    ai_base_url: str = os.getenv("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    ai_api_key: str = os.getenv("AI_API_KEY", "")
    ai_vision_model: str = os.getenv("AI_VISION_MODEL", "")
    ai_text_model: str = os.getenv("AI_TEXT_MODEL", "")
    ai_vision_base_url: str = os.getenv("AI_VISION_BASE_URL", "").rstrip("/")
    ai_vision_api_key: str = os.getenv("AI_VISION_API_KEY", "")
    whisper_executable: str = os.getenv("WHISPER_EXECUTABLE", "whisper")
    whisper_model: str = os.getenv("WHISPER_MODEL", "large-v3-turbo")
    whisper_language: str = os.getenv("WHISPER_LANGUAGE", "Chinese")
    whisper_word_timestamps: bool = os.getenv("WHISPER_WORD_TIMESTAMPS", "true").lower() in {
        "1", "true", "yes", "on"
    }
    whisper_model_dir: str = os.getenv("WHISPER_MODEL_DIR", "")
    whisper_device: str = os.getenv("WHISPER_DEVICE", "")
    whisper_timeout_seconds: int = int(os.getenv("WHISPER_TIMEOUT_SECONDS", "1800"))
    comfy_timeout_seconds: int = int(os.getenv("COMFY_TIMEOUT_SECONDS", "7200"))
    default_comfy_url: str = os.getenv("DEFAULT_COMFY_URL", DEFAULT_COMFY_URL).rstrip("/")
    frontend_origins: tuple[str, ...] = tuple(
        origin.strip()
        for origin in os.getenv(
            "FRONTEND_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8080,http://127.0.0.1:8080",
        ).split(",")
        if origin.strip()
    )

    @property
    def ai_enabled(self) -> bool:
        return bool(self.ai_api_key and self.ai_text_model)

    @property
    def vision_enabled(self) -> bool:
        return bool(
            self.ai_vision_model
            and self.vision_api_key
            and (self.ai_vision_base_url or self.ai_base_url)
        )

    @property
    def vision_base_url(self) -> str:
        return self.ai_vision_base_url or self.ai_base_url

    @property
    def vision_api_key(self) -> str:
        if self.ai_vision_api_key:
            if (
                self.ai_vision_base_url
                and self.ai_vision_base_url != self.ai_base_url
                and self.ai_vision_api_key == self.ai_api_key
            ):
                return ""
            return self.ai_vision_api_key
        if self.ai_vision_base_url and self.ai_vision_base_url != self.ai_base_url:
            return ""
        return self.ai_api_key

    @property
    def asr_enabled(self) -> bool:
        return self.whisper_path is not None

    @property
    def whisper_path(self) -> str | None:
        value = self.whisper_executable.strip()
        if not value:
            return None
        candidate = Path(value)
        if candidate.is_absolute() or candidate.parent != Path("."):
            return str(candidate) if candidate.is_file() else None
        return shutil.which(value)


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
