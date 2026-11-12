"""Local STT demo endpoint; does not change the team's session/stream contract."""
import io
import logging
from time import perf_counter
from av.error import FFmpegError
 
import av
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
 
from app.stt import STTConfig
from app.stt.audio import Audio
from app.stt.runtime import runtime
 
router = APIRouter(prefix="/api/stt", tags=["STT prototype"])
_lock = runtime.lock
MAX_BYTES = 10 * 1024 * 1024
MAX_SAMPLES = 55 * 16000
 
 
def decode(data: bytes) -> Audio:
    """Decode browser WebM/MP4 or uploaded audio; bound decoded duration too."""
    chunks = []
    count = 0
    try:
        with av.open(io.BytesIO(data), mode="r") as container:
            resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
            for frame in container.decode(audio=0):
                for converted in resampler.resample(frame):
                    count += converted.samples
                    if count > MAX_SAMPLES:
                        raise ValueError("55초 이하의 음성을 사용하세요.")
                    chunks.append(converted.to_ndarray().astype("<i2").tobytes())
            for converted in resampler.resample(None):
                count += converted.samples
                if count > MAX_SAMPLES:
                    raise ValueError("55초 이하의 음성을 사용하세요.")
                chunks.append(converted.to_ndarray().astype("<i2").tobytes())
    except (FFmpegError, IndexError):
        raise ValueError("읽을 수 없는 음성 파일입니다. WAV, WebM, MP3, M4A를 사용하세요.") from None
    return Audio(b"".join(chunks))
 
 
@router.post("/transcribe")
def transcribe(file: UploadFile = File(...), provider: str = Form("faster-whisper"),
               model: str = Form("small")):
    allowed = {"faster-whisper": {"tiny", "base", "small"}, "google": {"default"}, "clova": {"domain-default"}}
    if provider not in allowed or model not in allowed[provider]:
        raise HTTPException(400, "지원하지 않는 STT 제공자 또는 모델입니다.")
    data = file.file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "파일은 10 MB 이하여야 합니다.")
    if not _lock.acquire(blocking=False):
        raise HTTPException(409, "다른 음성을 처리 중입니다. 완료 후 다시 시도하세요.")
    started = perf_counter()
    try:
        audio = decode(data)
        config = STTConfig(provider=provider, model=model)
        service, cache_hit = runtime.create_service(config)
        initialization_ms = service.initialization_ms
        segments, metrics = service.transcribe(audio)
        return {"provider": provider, "model": model,
                "segments": [s.model_dump() for s in segments],
                "metrics": {**metrics, "initialization_ms": initialization_ms,
                            "total_ms": (perf_counter() - started) * 1000, "cache_hit": cache_hit}}
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    except Exception:
        logging.getLogger(__name__).warning("STT demo request failed (provider=%s)", provider)
        raise HTTPException(502, "STT 실행에 실패했습니다. 모델 다운로드 연결 또는 서버의 API 인증 설정을 확인하세요.") from None
    finally:
        _lock.release()
