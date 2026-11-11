"""STT-specific wire models; do not add PDF/search models here."""
from typing import Literal
from pydantic import BaseModel, Field, model_validator
 
 
class STTSegment(BaseModel):
    segment_id: str
    text: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    is_final: bool


class StartRequest(BaseModel):
    provider: Literal["faster-whisper", "google", "clova"] = "faster-whisper"
    model: str | None = None
    language: Literal["ko", "en", "ja"] = "ko"
    chunk_seconds: float = Field(default=2, ge=0.5, le=10, allow_inf_nan=False,
                                 description="faster-whisper 전용 분할 길이. Google/CLOVA는 100ms PCM 스트리밍을 사용합니다.")
    duration_seconds: float = Field(default=30, ge=1, le=1800, allow_inf_nan=False)
    input_device: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def provider_settings(self):
        defaults = {"faster-whisper": "tiny", "google": "default", "clova": "domain-default"}
        allowed = {"faster-whisper": {"tiny", "base", "small"}, "google": {"default"},
                   "clova": {"domain-default"}}
        self.model = self.model or defaults[self.provider]
        if self.model not in allowed[self.provider]:
            raise ValueError("provider에 맞는 model을 사용하거나 model을 null로 설정하세요.")
        if self.provider == "google" and self.duration_seconds > 240:
            raise ValueError("Google 스트리밍 테스트는 최대 240초입니다. 새 세션으로 재시작하세요.")
        return self

    model_config = {"extra": "forbid", "json_schema_extra": {"examples": [
        {"provider": provider, "model": None, "language": "ko", "chunk_seconds": 2,
         "duration_seconds": 30, "input_device": None}
        for provider in ("google", "clova", "faster-whisper")]}}

