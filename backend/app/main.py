from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from app.api.mock import router as mock_router
from app.api.search import router as search_router
from app.api.documents import router as documents_router
from app.api.integration import router as integration_router

app = FastAPI(
    title="SpeakSpot API",
    description="Real-time Speech-Based Document Highlighting System",
    version="0.1.0"
)


@app.get("/api/health")
def health_check():
    return {
        "status": "ok",
        "service": "SpeakSpot",
        "message": "Backend connected successfully"
    }


app.include_router(mock_router)
app.include_router(search_router)

app.include_router(documents_router)
app.include_router(integration_router)


# 팀 명세서의 단일 오류 응답 형식을 기본 서버에 적용한다.
@app.exception_handler(HTTPException)
async def http_exception_handler(_request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={
        'error': {'code': 'HTTP_ERROR', 'message': str(exc.detail), 'details': None}
    })


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request: Request, _exc: RequestValidationError):
    return JSONResponse(status_code=422, content={
        'error': {'code': 'VALIDATION_ERROR', 'message': '요청 형식이 올바르지 않습니다.', 'details': None}
    })
