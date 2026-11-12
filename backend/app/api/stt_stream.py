"""PCM WebSocket adapter; the team can reuse serve_stt_socket after session auth."""
import asyncio
import json
import os
import queue
from time import monotonic

from pydantic import ValidationError
from app.schemas.stt_models import StartRequest
from app.stt.cloud_socket import CloudSocketBridge
from app.stt.cloud_stream import CloudSetupError
from uuid import uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.stt import STTConfig
from app.stt.runtime import runtime
from app.stt.streaming import StreamingSTT

router = APIRouter()
MAX_FRAME_BYTES = 32000  # At most one second of 16kHz mono int16 per message.
MAX_PENDING_BYTES = 320000  # Ten seconds, including the block being processed.


class StreamError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


async def blocking_call(function, *args):
    """Never release the model lease while a cancelled thread is still using it."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        finally:
            raise


async def send(ws, event):
    await asyncio.wait_for(ws.send_json(event), timeout=10)


async def receive_start(ws):
    try:
        first = await asyncio.wait_for(ws.receive_json(), timeout=15)
    except (json.JSONDecodeError, KeyError, TypeError):
        raise StreamError("INVALID_FORMAT", "session.start JSON을 먼저 보내세요.") from None
    expected = {"encoding": "pcm_s16le", "sample_rate": 16000, "channels": 1}
    if not isinstance(first, dict) or first.get("type") != "session.start" or first.get("payload") != expected:
        raise StreamError("INVALID_FORMAT", "pcm_s16le / 16000 Hz / mono 형식이 필요합니다.")


async def serve_stt_socket(ws, service, session_id, chunk_seconds=2, duration_seconds=1800):
    """Accepts an already accepted socket and an exclusively leased STTService.

    First client message is session.start; session.ready then enables PCM input.
    On normal session.stop, flushes the tail and closes with code 1000.
    Authentication/session lookup and error-to-event mapping belong to the caller.
    """
    await receive_start(ws)
    stream = StreamingSTT(service, chunk_seconds)
    pending = asyncio.Queue(maxsize=128)
    pending_bytes = 0
    total_bytes = 0
    await send(ws, {"type": "session.ready", "payload": {"session_id": session_id}})

    async def receive():
        nonlocal pending_bytes, total_bytes
        while True:
            message = await asyncio.wait_for(ws.receive(), timeout=15)
            if message["type"] == "websocket.disconnect":
                raise WebSocketDisconnect(message.get("code", 1000))
            data = message.get("bytes")
            if data is not None:
                if not data or len(data) % 2 or len(data) > MAX_FRAME_BYTES:
                    raise StreamError("INVALID_AUDIO", "PCM 프레임은 짝수 바이트이며 1~32000바이트여야 합니다.")
                if data.startswith((b"RIFF", b"\x1aE\xdf\xa3")):
                    raise StreamError("INVALID_AUDIO", "WAV/WebM 헤더 없는 raw PCM을 보내세요.")
                total_bytes += len(data)
                pending_bytes += len(data)
                if total_bytes > int(32000 * duration_seconds):
                    raise StreamError("SESSION_LIMIT", "설정한 음성 길이 제한을 초과했습니다.")
                if pending_bytes > MAX_PENDING_BYTES:
                    raise StreamError("BACKPRESSURE", "인식이 입력을 따라가지 못합니다. 더 작은 모델로 재시작하세요.")
                item = data
            else:
                try:
                    event = json.loads(message.get("text", ""))
                except (ValueError, TypeError):
                    raise StreamError("INVALID_MESSAGE", "session.stop JSON 또는 PCM 바이너리만 보내세요.") from None
                if not isinstance(event, dict) or event.get("type") != "session.stop" or event.get("payload") != {}:
                    raise StreamError("INVALID_MESSAGE", "종료는 session.stop / payload={} 형식입니다.")
                item = None
            try:
                pending.put_nowait(item)
            except asyncio.QueueFull:
                raise StreamError("BACKPRESSURE", "입력 메시지 대기열이 가득 찼습니다.") from None
            if item is None:
                return

    async def process():
        nonlocal pending_bytes
        while True:
            data = await pending.get()
            batches = await blocking_call(stream.finish if data is None else stream.feed,
                                          *(() if data is None else (data,)))
            if data is not None:
                pending_bytes -= len(data)
            for segments, metrics in batches:
                for segment in segments:
                    await send(ws, {"type": "stt.result", "payload": segment.model_dump()})
            if data is None:
                return

    receiver = asyncio.create_task(receive())
    processor = asyncio.create_task(process())
    try:
        done, _ = await asyncio.wait([receiver, processor], timeout=duration_seconds + 15,
                                     return_when=asyncio.FIRST_COMPLETED)
        if not done:
            raise StreamError("SESSION_LIMIT", "세션 제한 시간이 지났습니다.")
        for task in done:
            task.result()
        await processor  # Normal stop drains all accepted audio, including the tail.
        await ws.close(code=1000)
    finally:
        for task in (receiver, processor):
            if not task.done():
                task.cancel()
        await asyncio.gather(receiver, processor, return_exceptions=True)


async def serve_cloud_socket(ws, settings, session_id):
    """Same PCM wire protocol as Whisper; cloud interim results retain their IDs."""
    await receive_start(ws)
    opening = asyncio.create_task(asyncio.to_thread(CloudSocketBridge, settings))
    try:
        bridge = await asyncio.shield(opening)
    except asyncio.CancelledError:
        # A disconnect during credential setup must not leak the newly opened channel.
        bridge = await opening
        await blocking_call(bridge.close)
        raise
    tasks = []
    try:
        bridge.worker.start()
        await bridge.wait_ready()
        await send(ws, {"type": "session.ready", "payload": {
            "session_id": session_id, "provider": settings.provider,
            "duration_seconds": settings.duration_seconds}})
        total_bytes = 0
        deadline = monotonic() + settings.duration_seconds
        stop_at = None

        async def receive():
            nonlocal total_bytes, stop_at
            while True:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    bridge.finish()
                    stop_at = monotonic()
                    return
                try:
                    message = await asyncio.wait_for(ws.receive(), min(15, remaining))
                except TimeoutError:
                    # Windows timer resolution may wake slightly before deadline.
                    # The timeout chosen here already represents the session cap.
                    if remaining <= 15:
                        bridge.finish()
                        stop_at = monotonic()
                        return
                    raise
                if message['type'] == 'websocket.disconnect':
                    raise WebSocketDisconnect(message.get('code', 1000))
                data = message.get('bytes')
                if data is not None:
                    if (not data or len(data) % 2 or len(data) > MAX_FRAME_BYTES
                            or data.startswith((b'RIFF', b'\x1aE\xdf\xa3'))):
                        raise StreamError('INVALID_AUDIO', '1~32000바이트의 짝수 길이 raw PCM을 보내세요.')
                    total_bytes += len(data)
                    if total_bytes > int(settings.duration_seconds * 32000):
                        raise StreamError('SESSION_LIMIT', '설정한 음성 길이 제한을 초과했습니다.')
                    try:
                        bridge.feed(data)
                    except CloudSetupError as error:
                        raise StreamError('BACKPRESSURE', str(error)) from None
                else:
                    try:
                        event = json.loads(message.get('text', ''))
                    except (ValueError, TypeError):
                        event = None
                    if not isinstance(event, dict) or event.get('type') != 'session.stop' or event.get('payload') != {}:
                        raise StreamError('INVALID_MESSAGE', '종료는 session.stop / payload={} 형식입니다.')
                    bridge.finish()
                    stop_at = monotonic()
                    return

        async def process():
            while True:
                bridge.check_failure()
                try:
                    segment = bridge.results.get_nowait()
                except queue.Empty:
                    if bridge.done.is_set():
                        bridge.check_failure()
                        return
                    if stop_at is not None and monotonic() - stop_at > 15:
                        raise StreamError('TIMEOUT', '마지막 클라우드 응답 대기 시간이 초과되었습니다.')
                    await asyncio.sleep(.01)
                    continue
                await send(ws, {'type': 'stt.result', 'payload': segment.model_dump()})

        receiver = asyncio.create_task(receive())
        processor = asyncio.create_task(process())
        tasks = [receiver, processor]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
        if not receiver.done():
            raise CloudSetupError('클라우드 연결이 예상보다 일찍 종료되었습니다.')
        await asyncio.wait_for(processor, timeout=15)
        await ws.close(code=1000)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await blocking_call(bridge.close)


@router.websocket("/api/stt/stream")
async def local_stream(ws: WebSocket, model: str | None = None, chunk_seconds: float = 2,
                       provider: str = "faster-whisper", language: str = "ko",
                       duration_seconds: float | None = None):
    # This adapter is local-only. Team integration must use their own auth/session route.
    origin = ws.headers.get("origin")
    host = ws.headers.get("host")
    allowed_origins = {f"http://{host}", f"https://{host}"}
    allowed_origins.update(value.strip() for value in
                          os.environ.get("STT_ALLOWED_ORIGINS", "").split(",") if value.strip())
    if (not ws.client or ws.client.host not in {"127.0.0.1", "::1", "testclient"}
            or (origin and origin not in allowed_origins)):
        await ws.close(code=1008)
        return
    await ws.accept()
    acquired = False
    try:
        try:
            settings = StartRequest(provider=provider, model=model, language=language,
                chunk_seconds=chunk_seconds, duration_seconds=(duration_seconds if duration_seconds is not None
                    else (240 if provider == "google" else 1800)))
        except ValidationError:
            raise StreamError("INVALID_CONFIG", "provider/model/language/시간 설정을 확인하세요. Google은 최대 240초입니다.") from None
        acquired = runtime.lock.acquire(blocking=False)
        if not acquired:
            raise StreamError("STT_BUSY", "다른 STT 작업이 진행 중입니다.")
        if settings.provider == "faster-whisper":
            service, _ = await blocking_call(runtime.create_service, STTConfig(
                model=settings.model, language=settings.language, beam_size=1))
            await serve_stt_socket(ws, service, uuid4().hex, chunk_seconds, settings.duration_seconds)
        else:
            await serve_cloud_socket(ws, settings, uuid4().hex)
    except WebSocketDisconnect:
        pass
    except Exception as error:
        if isinstance(error, StreamError):
            code, message = error.code, str(error)
        elif isinstance(error, CloudSetupError):
            code, message = "CLOUD_FAILED", str(error)
        elif isinstance(error, TimeoutError):
            code, message = "TIMEOUT", "입력 또는 결과 수신 대기 시간이 초과되었습니다."
        else:
            code, message = "STT_FAILED", "음성 인식 처리에 실패했습니다. 서버 설정을 확인하세요."
        try:
            await send(ws, {"type": "error", "payload": {"code": code, "message": message}})
            await ws.close(code=1011 if code in {"STT_FAILED", "CLOUD_FAILED"} else 1008)
        except (RuntimeError, WebSocketDisconnect, OSError, TimeoutError):
            pass
    finally:
        if acquired:
            runtime.lock.release()
