import math
from time import perf_counter
 
from app.schemas.stt_models import STTSegment
from .audio import Audio
from .config import STTConfig
from .providers import Provider, create_provider
 
 
class STTService:
    """One instance per session; call serially, with non-overlapping PCM chunks."""
    def __init__(self, config: STTConfig, provider: Provider | None = None):
        self.config = config
        started = perf_counter()
        self.provider = provider if provider is not None else create_provider(config)
        self.initialization_ms = (perf_counter() - started) * 1000
        self._counter = 0
        self._samples = 0
 
    def transcribe(self, audio: Audio) -> tuple[list[STTSegment], dict]:
        started = perf_counter()
        utterances = list(self.provider.transcribe(audio))
        offset = self._samples / 16
        results = []
        next_id = self._counter
        previous_start = -1
        for item in utterances:
            if not item.text.strip():
                continue
            if (not math.isfinite(item.start_ms) or not math.isfinite(item.end_ms)
                    or item.start_ms < 0 or item.end_ms < item.start_ms
                    or item.start_ms < previous_start or item.end_ms > audio.duration_ms + 100):
                raise ValueError("STT 제공자가 유효하지 않은 구간 시각을 반환했습니다.")
            previous_start = item.start_ms
            next_id += 1
            results.append(STTSegment(segment_id=f"seg_{next_id:06d}", text=item.text.strip(),
                start_ms=round(offset + min(item.start_ms, audio.duration_ms)),
                end_ms=round(offset + min(item.end_ms, audio.duration_ms)), is_final=True))
        elapsed = (perf_counter() - started) * 1000
        # Commit only after a complete successful call, making failed chunks retryable.
        self._counter = next_id
        self._samples += len(audio.pcm) // 2
        return results, {"processing_ms": elapsed, "audio_ms": audio.duration_ms,
                         "rtf": elapsed / audio.duration_ms}
