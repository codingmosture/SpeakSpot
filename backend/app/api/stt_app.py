from fastapi import FastAPI
from contextlib import asynccontextmanager
from starlette.concurrency import run_in_threadpool
from app.api.stt import router as stt_router
from app.api.stt_live import router as live_router, shutdown
from app.api.stt_stream import router as stream_router


@asynccontextmanager
async def lifespan(app):
    yield
    await run_in_threadpool(shutdown)
 
app = FastAPI(title="SpeakSpot STT Study", lifespan=lifespan,
    description="PC 마이크 테스트: /api/stt/live/start → /results → /stop. "
    "클라이언트 PCM 연결 테스트: WS /api/stt/stream (Swagger에서 직접 실행할 수 없음). "
    "이 서버는 STT 단독 통합 실험용이며 한 번에 한 작업을 처리합니다.")
app.include_router(stt_router)
app.include_router(live_router)
app.include_router(stream_router)
 
@app.get("/api/health")
def health():
    return {"status": "ok"}
