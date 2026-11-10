from fastapi import APIRouter, UploadFile, File, Query, status
from fastapi.responses import FileResponse, JSONResponse
from app.schemas.models import Document, DocumentBlocksResponse
from app.documents.service import document_service

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


def error_response(status_code: int, code: str, message: str):
    # 명세서 6번 오류 응답 규격에 맞춘 헬퍼 함수
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": None}}
    )


# 3.1 PDF 업로드 (POST /api/v1/documents)
@router.post("", response_model=Document, status_code=status.HTTP_201_CREATED)
async def upload_document(file: UploadFile = File(...)):
    # PDF 파일이 아닌 경우 415 에러 반환
    if file.content_type != "application/pdf":
        return error_response(415, "UNSUPPORTED_MEDIA_TYPE", "PDF 파일만 업로드할 수 있습니다.")
    
    try:
        doc_info = await document_service.process_upload(file)
        return doc_info
    except Exception as e:
        return error_response(500, "INTERNAL_SERVER_ERROR", f"PDF 처리 중 오류가 발생했습니다: {str(e)}")


# 3.2 블록 조회 (GET /api/v1/documents/{document_id}/blocks)
@router.get("/{document_id}/blocks", response_model=DocumentBlocksResponse)
async def get_document_blocks(
    document_id: str,
    page: int | None = Query(default=None, ge=1, description="조회할 페이지 번호 (1-based)")
):
    blocks = document_service.get_blocks(document_id, page)
    if blocks is None:
        return error_response(404, "DOCUMENT_NOT_FOUND", "해당 PDF 문서를 찾을 수 없습니다.")
    
    return DocumentBlocksResponse(document_id=document_id, blocks=blocks)


# 3.3 원본 PDF 조회 (GET /api/v1/documents/{document_id}/file)
@router.get("/{document_id}/file", response_class=FileResponse)
async def get_document_file(document_id: str):
    file_path = document_service.get_file_path(document_id)
    if not file_path:
        return error_response(404, "DOCUMENT_NOT_FOUND", "해당 PDF 문서를 찾을 수 없습니다.")
    
    return FileResponse(file_path, media_type="application/pdf", filename=f"{document_id}.pdf")