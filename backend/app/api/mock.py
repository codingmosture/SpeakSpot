from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.schemas.models import Block, SearchResult

router = APIRouter(prefix="/api", tags=["Mock APIs"])


# 검색 요청 형식
class SearchRequest(BaseModel):
    document_id: str
    session_id: str
    text: str = Field(min_length=1)
    segment_id: str | None = None


# 임시 PDF 블록
MOCK_BLOCK = Block(
    block_id="p0005_b0003",
    page=5,
    text="운영체제는 프로세스를 관리한다.",
    block_type="paragraph",
    bbox=(0.1, 0.2, 0.8, 0.3),
    rects=[(0.1, 0.2, 0.8, 0.3)],
    keywords=["운영체제", "프로세스"]
)


# PDF 블록 목록 반환
@router.get("/mock/blocks", response_model=list[Block])
def get_mock_blocks():
    return [MOCK_BLOCK]


# 탐색 결과 반환 (임시 로직)
@router.post("/search", response_model=SearchResult)
def search(request: SearchRequest):
    matched = (
        "운영체제" in request.text
        or "프로세스" in request.text
    )

    return SearchResult(
        status="MATCH" if matched else "NONE",
        document_id=request.document_id,
        session_id=request.session_id,
        segment_id=request.segment_id,
        page=MOCK_BLOCK.page if matched else None,
        block_id=MOCK_BLOCK.block_id if matched else None,
        score=0.87 if matched else None,
        strategy="local",
        stage_used="local" if matched else "none",
        model="mock",
        processing_ms=0.0
    )
