from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

EMOTION_KEYS = ("Happy", "Angry", "Sad", "Fear", "Hate", "Low", "Surprise", "Neutral")
TTSEngine = Literal["indextts2_legacy", "indextts2_voice_clone"]
TTS_ENGINES = {"indextts2_legacy", "indextts2_voice_clone"}
SubtitlePosition = Literal["top", "center", "bottom", "custom"]


class EmotionVector(BaseModel):
    Happy: float = Field(0.15, ge=0, le=1)
    Angry: float = Field(0, ge=0, le=1)
    Sad: float = Field(0, ge=0, le=1)
    Fear: float = Field(0, ge=0, le=1)
    Hate: float = Field(0, ge=0, le=1)
    Low: float = Field(0.08, ge=0, le=1)
    Surprise: float = Field(0.03, ge=0, le=1)
    Neutral: float = Field(0.65, ge=0, le=1)


class SpeechEvent(BaseModel):
    text: str
    full_sentence: str
    absolute_start: float
    absolute_end: float
    local_start: float
    local_end: float
    enters_before: bool = False
    continues_after: bool = False


class Segment(BaseModel):
    index: int
    start: float
    end: float
    spoken_text: str
    action_prompt: str
    start_state: str = "正视镜头，自然浅笑"
    end_state: str = "正视镜头，姿态自然"
    motion_strength: float = Field(0.3, ge=0, le=1)
    sentence_context: str = ""
    starts_mid_sentence: bool = False
    ends_mid_sentence: bool = False
    speech_events: list[SpeechEvent] = Field(default_factory=list)

    @field_validator("action_prompt")
    @classmethod
    def action_prompt_must_be_one_line(cls, value: str) -> str:
        value = re.sub(r"\s+", " ", value).strip()
        if not value:
            raise ValueError("动作提示词不能为空")
        return value


class ProjectPatch(BaseModel):
    script: str | None = None
    original_script: str | None = None
    emotion: EmotionVector | None = None
    segments: list[Segment] | None = None
    title: str | None = None
    tts_engine: TTSEngine | None = None
    bgm_enabled: bool | None = None
    bgm_volume: float | None = Field(default=None, ge=0, le=1)
    bgm_ducking: bool | None = None
    bgm_fade_in: float | None = Field(default=None, ge=0, le=30)
    bgm_fade_out: float | None = Field(default=None, ge=0, le=30)
    subtitle_enabled: bool | None = None
    subtitle_font_name: str | None = Field(default=None, max_length=100)
    subtitle_font_size: int | None = Field(default=None, ge=12, le=160)
    subtitle_font_bold: bool | None = None
    subtitle_font_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    subtitle_position: SubtitlePosition | None = None
    subtitle_custom_position: float | None = Field(default=None, ge=0, le=100)
    subtitle_stroke_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    subtitle_stroke_width: float | None = Field(default=None, ge=0, le=12)
    subtitle_background_enabled: bool | None = None
    subtitle_background_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    subtitle_background_opacity: int | None = Field(default=None, ge=0, le=100)
    subtitle_max_chars: int | None = Field(default=None, ge=6, le=32)
    video_title_enabled: bool | None = None
    video_title: str | None = Field(default=None, max_length=100)
    video_title_font_name: str | None = Field(default=None, max_length=100)
    video_title_font_size: int | None = Field(default=None, ge=24, le=180)
    video_title_primary_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    video_title_secondary_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    video_title_position: float | None = Field(default=None, ge=0, le=50)
    video_title_stroke_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    video_title_stroke_width: float | None = Field(default=None, ge=0, le=12)


class TaskPatch(BaseModel):
    title: str | None = Field(default=None, max_length=100)
    original_script: str | None = None


class ComfyCheckRequest(BaseModel):
    url: str
    tts_engine: TTSEngine = "indextts2_legacy"


class AppSettingsPatch(BaseModel):
    comfy_url: str


class ProjectCreate(BaseModel):
    original_script: str
    title: str = "未命名项目"
    purpose: str = "品牌口播"
    audience: str = "普通观众"
    requested_style: str = "专业、温和、可信赖"
    tts_engine: TTSEngine = "indextts2_legacy"
    auto_run: bool = False
    expect_image_upload: bool = False
    expect_voice_upload: bool = False
    expect_emotion_voice_upload: bool = False
    bgm_enabled: bool = False
    bgm_volume: float = Field(default=0.25, ge=0, le=1)
    bgm_ducking: bool = True
    bgm_fade_in: float = Field(default=1.5, ge=0, le=30)
    bgm_fade_out: float = Field(default=2.0, ge=0, le=30)
    expect_bgm_upload: bool = False
    subtitle_enabled: bool = True
    subtitle_font_name: str = Field(default="Microsoft YaHei", max_length=100)
    subtitle_font_size: int = Field(default=64, ge=12, le=160)
    subtitle_font_bold: bool = True
    subtitle_font_color: str = Field(default="#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$")
    subtitle_position: SubtitlePosition = "custom"
    subtitle_custom_position: float = Field(default=73, ge=0, le=100)
    subtitle_stroke_color: str = Field(default="#000000", pattern=r"^#[0-9A-Fa-f]{6}$")
    subtitle_stroke_width: float = Field(default=3, ge=0, le=12)
    subtitle_background_enabled: bool = False
    subtitle_background_color: str = Field(default="#000000", pattern=r"^#[0-9A-Fa-f]{6}$")
    subtitle_background_opacity: int = Field(default=40, ge=0, le=100)
    subtitle_max_chars: int = Field(default=14, ge=6, le=32)
    video_title_enabled: bool = True
    video_title: str = Field(default="", max_length=100)
    video_title_font_name: str = Field(default="Microsoft YaHei", max_length=100)
    video_title_font_size: int = Field(default=88, ge=24, le=180)
    video_title_primary_color: str = Field(default="#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$")
    video_title_secondary_color: str = Field(default="#FFD84D", pattern=r"^#[0-9A-Fa-f]{6}$")
    video_title_position: float = Field(default=10, ge=0, le=50)
    video_title_stroke_color: str = Field(default="#000000", pattern=r"^#[0-9A-Fa-f]{6}$")
    video_title_stroke_width: float = Field(default=4, ge=0, le=12)


class ApiEnvelope(BaseModel):
    ok: bool = True
    data: Any = None
    message: str = ""
