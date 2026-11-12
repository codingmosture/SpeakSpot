
from typing import Literal
from pydantic import BaseModel, Field, model_validator


# PDF 좌표: [x0, y0, x1, y1]
BBox = tuple[float, float, float, float]


# 1. PDF 문서 정보
class Document(BaseModel):
    document_id: str
    file_name: str
    page_count: int = Field(gt=0)
    status: Literal["processing", "ready", "failed"]


# 2. PDF 블록 정보
class Block(BaseModel):
    block_id: str
    page: int = Field(ge=1)
    text: str
    block_type: Literal[
        "title", "paragraph", "bullet",
        "table", "caption", "equation", "other"
    ]
    bbox: BBox
    rects: list[BBox]
    keywords: list[str] = Field(default_factory=list)


# 3. STT 음성 인식 결과
# Canonical STT schema; preserve existing team imports.
from app.schemas.stt_models import STTSegment


# 4. 탐색 결과
class SearchResult(BaseModel):
    status: Literal["MATCH", "NONE"]
    document_id: str
    session_id: str
    segment_id: str | None
    page: int | None
    block_id: str | None
    score: float | None
    strategy: Literal["local", "global", "hybrid"]
    stage_used: Literal["local", "global", "none"]
    model: str
    processing_ms: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_result(self):
        if self.status == "MATCH":
            if self.page is None or self.block_id is None:
                raise ValueError("MATCH에는 페이지와 블록 ID가 필요합니다.")
            if self.page < 1:
                raise ValueError("페이지는 1 이상이어야 합니다.")

        if self.status == "NONE":
            if self.page is not None or self.block_id is not None:
                raise ValueError("NONE은 위치 정보가 없어야 합니다.")

        return self


# 5. 모델 설정
class SessionConfig(BaseModel):
    stt_model: str
    search_model: str
    search_strategy: Literal["local", "global", "hybrid"]
    local_radius_pages: int = Field(ge=0)
    stt_buffer_seconds: float = Field(gt=0)
    stt_update_interval_seconds: float = Field(gt=0)
