"""Local-PC microphone testing through Swagger, not browser audio streaming."""
import copy
import queue
import threading
from collections import deque
from time import perf_counter
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from app.schemas.stt_models import StartRequest

from app.stt import STTConfig
from app.stt.live import MicrophoneBuffer, PCMChunks, consume
from app.stt.runtime import runtime


def local_only(request: Request):
    if request.client is None or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(403, "현재 PC의 localhost에서만 사용할 수 있습니다.")
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "같은 서버의 /docs에서 실행하세요.")


router = APIRouter(prefix="/api/stt/live", tags=["실시간 STT — PC 마이크"],
                   dependencies=[Depends(local_only)])




class LiveSession:
    def __init__(self, settings):
        self.session_id = uuid4().hex
        self.settings = settings
        self.capture = MicrophoneBuffer()
        self.lock = threading.Lock()
        self.state = "starting"
        self.error = None
        self.chunks = deque(maxlen=150)
        self.total_chunks = 0
        self.initialization_ms = None
        self.cache_hit = False
        self.audio_ms = 0
        self.processing_ms = 0
        self.recording_started = None
        self.partial = None
        self.thread = threading.Thread(target=self.work, daemon=True)

    def set_state(self, state):
        with self.lock:
            self.state = state
            if state == 'recording':
                self.recording_started = perf_counter()

    def sent_audio(self, audio_ms):
        with self.lock:
            self.audio_ms += audio_ms

    def emit_cloud(self, segment):
        with self.lock:
            self.total_chunks += 1
            self.partial = None if segment.is_final else segment.model_dump()
            delay = (max(0, (perf_counter() - self.recording_started) * 1000 - segment.end_ms)
                     if self.recording_started is not None else None)
            self.chunks.append({'chunk_id': self.total_chunks, 'segments': [segment.model_dump()],
                                'metrics': {'result_delay_ms': delay, 'processing_ms': None, 'rtf': None}})

    def emit(self, segments, metrics, captured_at):
        with self.lock:
            self.total_chunks += 1
            self.audio_ms += metrics.get("audio_ms", 0)
            self.processing_ms += metrics.get("processing_ms", 0)
            self.chunks.append({"chunk_id": self.total_chunks,
                "segments": [s.model_dump() for s in segments],
                "metrics": {**metrics,
                    "lag_ms": max(0, (perf_counter() - captured_at) * 1000)}})

    def snapshot(self, after_chunk=0):
        with self.lock:
            return {"session_id": self.session_id, "state": self.state,
                    "settings": self.settings.model_dump(), "error": self.error,
                    "transport": "chunked-local" if self.settings.provider == 'faster-whisper' else 'native-grpc',
                    "partial": copy.deepcopy(self.partial),
                    "last_chunk_id": self.total_chunks,
                    "history_truncated": bool(self.chunks and after_chunk < self.chunks[0]["chunk_id"] - 1),
                    "summary": {"audio_ms": self.audio_ms,
                        "processing_ms": self.processing_ms if self.settings.provider == 'faster-whisper' else None,
                        "rtf": self.processing_ms / self.audio_ms if self.audio_ms and self.settings.provider == 'faster-whisper' else None,
                        "initialization_ms": self.initialization_ms, "cache_hit": self.cache_hit},
                    "oldest_chunk_id": self.chunks[0]["chunk_id"] if self.chunks else None,
                    "results": copy.deepcopy([c for c in self.chunks if c["chunk_id"] > after_chunk])}

    def work(self):
        timer = None
        acquired = False
        try:
            acquired = runtime.lock.acquire(blocking=False)
            if not acquired:
                raise RuntimeError("다른 STT 작업이 진행 중입니다. 종료 후 다시 시작하세요.")
            import sounddevice as sd
            sd.check_input_settings(device=self.settings.input_device, channels=1,
                                    dtype="int16", samplerate=16000)
            if self.capture.stop.is_set():
                return
            if self.settings.provider != 'faster-whisper':
                from app.stt.cloud_microphone import run_cloud_microphone
                run_cloud_microphone(self.settings, self.capture, self.set_state,
                                     self.emit_cloud, self.sent_audio)
                return
            service, cache_hit = runtime.create_service(STTConfig(model=self.settings.model,
                                        language=self.settings.language, beam_size=1))
            with self.lock:
                self.initialization_ms = service.initialization_ms
                self.cache_hit = cache_hit
            if self.capture.stop.is_set():
                return
            chunks = PCMChunks(self.settings.chunk_seconds)
            captured_at = perf_counter()
            with sd.RawInputStream(samplerate=16000, blocksize=1600, channels=1,
                                   dtype="int16", device=self.settings.input_device,
                                   callback=self.capture.callback) as stream:
                self.set_state("stopping" if self.capture.stop.is_set() else "recording")
                timer = threading.Timer(self.settings.duration_seconds, self.capture.stop.set)
                timer.daemon = True
                timer.start()
                while not self.capture.stop.is_set():
                    try:
                        pcm, captured_at = self.capture.blocks.get(timeout=0.1)
                    except queue.Empty:
                        if not stream.active:
                            raise RuntimeError("마이크 연결이 끊어졌습니다.")
                        continue
                    for audio in chunks.feed(pcm):
                        self.emit(*service.transcribe(audio), captured_at)
            self.set_state("stopping")
            consume(self.capture.blocks, chunks, service, self.emit, captured_at)
            if self.capture.error:
                raise RuntimeError(self.capture.error)
        except Exception as error:
            with self.lock:
                if self.settings.provider != 'faster-whisper':
                    from app.stt.cloud_stream import safe_cloud_error
                    self.error = safe_cloud_error(error)
                else:
                    self.error = f"{type(error).__name__}: {error}"
                self.state = "error"
        finally:
            if timer:
                timer.cancel()
            if acquired:
                runtime.lock.release()
            with self.lock:
                if self.state != "error":
                    self.state = "stopped"


_guard = threading.Lock()
_current: LiveSession | None = None


@router.get("/devices", summary="0. 사용할 수 있는 PC 마이크 목록")
def devices():
    import sounddevice as sd
    try:
        default = sd.default.device[0]
        return [{"id": i, "name": d["name"], "default": i == default}
                for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]
    except Exception:
        raise HTTPException(503, "마이크 장치를 조회하지 못했습니다.") from None


@router.post("/start", status_code=202, summary="1. PC 마이크로 실시간 STT 시작",
             description="현재 PC의 마이크를 사용합니다. 시작 응답 후 GET /results를 실행해 "
             "state=recording인지 확인하고 말하세요. provider=google/clova는 실제 gRPC 스트리밍이며 "
             "서버 인증 설정이 필요합니다. model=null이면 제공자 기본값입니다. "
             "Google은 최대 240초, 나머지는 최대 1800초입니다. 기본 30초 뒤 종료됩니다.")
def start(settings: StartRequest):
    global _current
    with _guard:
        if _current and _current.thread.is_alive():
            raise HTTPException(409, "진행 중인 테스트가 있습니다. /stop 후 종료 상태를 확인하세요.")
        _current = LiveSession(settings)
        _current.thread.start()
        return _current.snapshot()


@router.get("/results", summary="2. 현재 상태와 누적 인식 결과 조회",
            description="Execute를 다시 눌러 최신 결과를 확인합니다. 자동 갱신되지 않습니다. "
            "최근 150개 청크만 보관하며, 새 테스트를 시작하면 이전 결과는 초기화됩니다.")
def results(after_chunk: int = 0, session_id: str | None = None):
    if after_chunk < 0:
        raise HTTPException(422, "after_chunk는 0 이상이어야 합니다.")
    with _guard:
        if _current is None:
            raise HTTPException(404, "/start로 먼저 테스트를 시작하세요.")
        if session_id is not None and session_id != _current.session_id:
            raise HTTPException(409, "새 테스트로 바뀌었습니다. session_id를 확인하세요.")
        return _current.snapshot(after_chunk)


@router.post("/stop", status_code=202, summary="3. 마이크 종료 요청",
             description="남은 음성 처리까지 완료되면 /results의 state가 stopped가 됩니다.")
def stop(session_id: str | None = None):
    with _guard:
        if _current is None:
            raise HTTPException(404, "시작한 테스트가 없습니다.")
        if session_id is not None and session_id != _current.session_id:
            raise HTTPException(409, "다른 테스트를 종료할 수 없습니다.")
        _current.capture.stop.set()
        with _current.lock:
            if _current.state in {"starting", "recording"}:
                _current.state = "stopping"
        return _current.snapshot()


def shutdown():
    with _guard:
        session = _current
        if session:
            session.capture.stop.set()
    if session:
        session.thread.join(timeout=10)
