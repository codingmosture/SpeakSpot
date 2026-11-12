"""팀의 문서 세션과 STT WebSocket을 연결하는 얇은 통합 라우터.

STT 구현은 건드리지 않고, 확정 stt.result 직후 SearchEngine을 호출하여
같은 연결에 highlight.update를 보낸다.
"""
import asyncio
import os
from uuid import uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.api.search import engine, session_configs
from app.api.stt_stream import (
    StreamError, blocking_call, serve_stt_socket, serve_cloud_socket, send,
)
from app.schemas.stt_models import StartRequest
from app.stt import STTConfig
from app.stt.cloud_stream import CloudSetupError
from app.stt.runtime import runtime

router = APIRouter()


class SearchSocket:
    """STT 전송을 가로채 STT 확정 문장에 대한 탐색 결과를 이어서 전송한다."""

    def __init__(self, websocket: WebSocket, session_id: str):
        self.websocket = websocket
        self.session_id = session_id
        # 같은 문서 세션에서 마이크를 다시 켜더라도 STT segment_id가 중복되지 않게 한다.
        self.recording_id = uuid4().hex[:12]

    async def send_json(self, event):
        if event.get("type") != "stt.result":
            await self.websocket.send_json(event)
            return
        segment = dict(event["payload"])
        segment["segment_id"] = f"{self.recording_id}_{segment['segment_id']}"
        await self.websocket.send_json({"type": "stt.result", "payload": segment})
        if segment["is_final"] and segment["text"].strip():
            result = await asyncio.to_thread(
                engine.search, self.session_id, segment["text"],
                segment["segment_id"], segment["end_ms"],
            )
            await self.websocket.send_json({
                "type": "highlight.update", "payload": result.model_dump(),
            })

    async def receive_json(self):
        return await self.websocket.receive_json()

    async def receive(self):
        return await self.websocket.receive()

    async def close(self, **kwargs):
        return await self.websocket.close(**kwargs)


def stt_settings(config):
    """SessionConfig.stt_model을 담당 STT 모듈의 제공자/모델로 변환한다."""
    name = config.stt_model
    if name.startswith("faster-whisper-"):
        provider, model = "faster-whisper", name.removeprefix("faster-whisper-")
    elif name in {"tiny", "base", "small"}:
        provider, model = "faster-whisper", name
    elif name in {"google", "clova"}:
        provider, model = name, None
    else:
        raise StreamError("INVALID_CONFIG", f"지원하지 않는 STT 모델: {name}")
    return StartRequest(
        provider=provider, model=model, language="ko",
        chunk_seconds=config.stt_update_interval_seconds,
        duration_seconds=240 if provider == "google" else 1800,
    )


@router.websocket("/api/v1/sessions/{session_id}/stream")
async def session_stream(ws: WebSocket, session_id: str):
    """PDF/검색 세션에 귀속된 클라이언트 PCM → STT → 탐색 → 하이라이트."""
    origin = ws.headers.get("origin")
    host = ws.headers.get("host")
    allowed_origins = {
        f"http://{host}", f"https://{host}",
        "http://localhost:5173", "http://127.0.0.1:5173",
    }
    allowed_origins.update(
        x.strip() for x in os.environ.get("STT_ALLOWED_ORIGINS", "").split(",") if x.strip()
    )
    if (not ws.client or ws.client.host not in {"127.0.0.1", "::1", "testclient"}
            or (origin and origin not in allowed_origins)):
        await ws.close(code=1008)
        return

    await ws.accept()
    acquired = False
    try:
        # 웹소켓이 존재하지 않는 문서 세션을 임의로 생성하지 않도록 검증.
        try:
            engine.get_current_page(session_id)
            config = session_configs[session_id]
        except KeyError:
            raise StreamError("SESSION_NOT_FOUND", "존재하지 않는 문서 세션입니다.") from None
        settings = stt_settings(config)
        acquired = runtime.lock.acquire(blocking=False)
        if not acquired:
            raise StreamError("STT_BUSY", "다른 STT 작업이 진행 중입니다.")
        socket = SearchSocket(ws, session_id)
        if settings.provider == "faster-whisper":
            service, _ = await blocking_call(runtime.create_service, STTConfig(
                model=settings.model, language=settings.language, beam_size=1))
            await serve_stt_socket(
                socket, service, session_id, settings.chunk_seconds, settings.duration_seconds)
        else:
            await serve_cloud_socket(socket, settings, session_id)
    except WebSocketDisconnect:
        pass
    except Exception as error:
        if isinstance(error, StreamError):
            code, message = error.code, str(error)
        elif isinstance(error, ValidationError):
            code, message = "INVALID_CONFIG", "STT 설정을 확인하세요."
        elif isinstance(error, CloudSetupError):
            code, message = "CLOUD_FAILED", str(error)
        elif isinstance(error, TimeoutError):
            code, message = "TIMEOUT", "음성 입력 또는 결과 수신 시간이 초과되었습니다."
        else:
            code, message = "STT_FAILED", "음성 인식 또는 문서 탐색에 실패했습니다."
        try:
            await send(ws, {"type": "error", "payload": {"code": code, "message": message}})
            await ws.close(code=1011 if code in {"STT_FAILED", "CLOUD_FAILED"} else 1008)
        except (RuntimeError, WebSocketDisconnect, OSError, TimeoutError):
            pass
    finally:
        if acquired:
            runtime.lock.release()
