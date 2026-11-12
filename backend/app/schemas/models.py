"""SpeakSpot 공통 계약. 기존 필드/Mock API와 호환되게 유지."""
from typing import Literal
from pydantic import BaseModel, Field, model_validator

BBox = tuple[float, float, float, float]


class Document(BaseModel):
    document_id: str
    file_name: str
    page_count: int = Field(gt=0)
    status: Literal['processing', 'ready', 'failed']


class Block(BaseModel):
    block_id: str
    page: int = Field(ge=1)
    text: str
    block_type: Literal['title', 'paragraph', 'bullet', 'table', 'caption', 'equation', 'other']
    bbox: BBox
    rects: list[BBox]
    keywords: list[str] = Field(default_factory=list)


class STTSegment(BaseModel):
    segment_id: str
    text: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    is_final: bool


SearchStrategyName = Literal['sliding_queue', 'local', 'global', 'hybrid1', 'hybrid2', 'hybrid']
SearchStageName = Literal['sliding_queue', 'local', 'global', 'none']


class SearchResult(BaseModel):
    status: Literal['MATCH', 'NONE']
    document_id: str
    session_id: str
    segment_id: str | None
    page: int | None
    block_id: str | None
    score: float | None
    strategy: SearchStrategyName
    stage_used: SearchStageName
    model: str
    processing_ms: float = Field(ge=0)

    @model_validator(mode='after')
    def validate_result(self):
        if self.status == 'MATCH':
            if self.page is None or self.block_id is None:
                raise ValueError('MATCH에는 페이지와 블록 ID가 필요합니다.')
            if self.page < 1:
                raise ValueError('페이지는 1 이상이어야 합니다.')
        if self.status == 'NONE':
            if self.page is not None or self.block_id is not None:
                raise ValueError('NONE은 위치 정보가 없어야 합니다.')
        return self


class SessionConfig(BaseModel):
    # 기존 API 요청 필드를 유지하고 신규 검색 알고리즘 설정을 추가한다.
    stt_model: str = 'not-configured'
    search_model: str | None = None
    search_strategy: SearchStrategyName = 'sliding_queue'
    local_radius_pages: int = Field(default=5, ge=0, le=100)
    stt_buffer_seconds: float = Field(default=5.0, gt=0)
    stt_update_interval_seconds: float = Field(default=2.0, gt=0)
    sliding_window_words: int = Field(default=40, ge=1, le=500)
    min_match_score: float = Field(default=0.34, ge=0, le=1)
    backward_penalty: float = Field(default=0.08, ge=0, le=1)
    stay_bonus: float = Field(default=0.025, ge=0, le=1)
    switch_margin: float = Field(default=0.05, ge=0, le=1)
    prefix_bonus: float = Field(default=0.035, ge=0, le=1)
    min_margin: float = Field(default=0.0, ge=0, le=1)
    global_top_k_pages: int = Field(default=3, ge=1, le=100)
    jev_min_confidence: float = Field(default=0.0, ge=0, le=1)
    max_jev_candidates: int = Field(default=120, ge=1, le=240)
