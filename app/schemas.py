from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .config import DEFAULT_COMFY_URL


EMOTION_KEYS = ("Happy", "Angry", "Sad", "Fear", "Hate", "Low", "Surprise", "Neutral")
TTSEngine = Literal["indextts2_legacy", "indextts2_voice_clone"]
TTS_ENGINES = {"indextts2_legacy", "indextts2_voice_clone"}


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
    comfy_url: str | None = None
    script: str | None = None
    emotion: EmotionVector | None = None
    segments: list[Segment] | None = None
    title: str | None = None
    tts_engine: TTSEngine | None = None


class ComfyCheckRequest(BaseModel):
    url: str
    tts_engine: TTSEngine = "indextts2_legacy"


class ProjectCreate(BaseModel):
    comfy_url: str = DEFAULT_COMFY_URL
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


class ApiEnvelope(BaseModel):
    ok: bool = True
    data: Any = None
    message: str = ""
