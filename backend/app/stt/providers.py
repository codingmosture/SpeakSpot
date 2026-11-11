"""Adapters return chunk-relative milliseconds; the service owns session IDs/time."""
import base64
import json
import os
import math
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse
 
from .audio import Audio
from .config import STTConfig
 
 
@dataclass(frozen=True)
class Utterance:
    text: str
    start_ms: float
    end_ms: float
 
 
class Provider(Protocol):
    def transcribe(self, audio: Audio) -> list[Utterance]: ...
 
 
class FasterWhisper:
    def __init__(self, config: STTConfig):
        from faster_whisper import WhisperModel
        self.config = config
        self.model = WhisperModel(config.model, device=config.device, compute_type=config.compute_type)
 
    def transcribe(self, audio: Audio) -> list[Utterance]:
        import numpy as np
        samples = np.frombuffer(audio.pcm, dtype="<i2").astype(np.float32) / 32768.0
        segments, _ = self.model.transcribe(
            samples, language=self.config.language, beam_size=self.config.beam_size,
            vad_filter=self.config.vad_filter, condition_on_previous_text=False,
        )
        result = []
        for segment in segments:
            start, end = segment.start * 1000, segment.end * 1000
            if not math.isfinite(start) or not math.isfinite(end) or end < start:
                raise ValueError("faster-whisper가 유효하지 않은 구간 시각을 반환했습니다.")
            # Whisper padding/VAD timestamp restoration can extend beyond a short input.
            # Normalize in this adapter; keep the service's strict checks for all providers.
            start = max(0, start)
            end = min(audio.duration_ms, end)
            if start < end:
                result.append(Utterance(segment.text, start, end))
        return result
 
 
def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"환경변수 {name}를 설정하세요.")
    return value
 
 
def request_json(url, *, timeout, **kwargs):
    import requests
    try:
        response = requests.post(url, timeout=timeout, **kwargs)
    except requests.RequestException:
        # Do not print credentials, endpoint secrets, or request audio.
        raise RuntimeError("STT 네트워크 요청에 실패했습니다. 연결 및 시간 제한을 확인하세요.") from None
    if not response.ok:
        raise RuntimeError(f"STT HTTP {response.status_code}: 인증, 할당량, 요청 설정을 확인하세요.")
    try:
        return response.json()
    except ValueError:
        raise RuntimeError("STT 서비스가 JSON 이외의 응답을 반환했습니다.") from None
 
 
class GoogleSTT:
    """Google Speech-to-Text v1 synchronous REST, short audio only."""
    def __init__(self, config):
        self.config = config
        self.token = required_env("GOOGLE_ACCESS_TOKEN")
        self.project = required_env("GOOGLE_CLOUD_PROJECT")
 
    def transcribe(self, audio):
        if audio.duration_ms >= 60000:
            raise ValueError("Google 동기 인식은 이 프로토타입에서 60초 미만 파일만 지원합니다.")
        data = request_json(
            "https://speech.googleapis.com/v1/speech:recognize", timeout=self.config.timeout_seconds,
            headers={"Authorization": f"Bearer {self.token}", "x-goog-user-project": self.project},
            json={"config": {"encoding": "LINEAR16", "sampleRateHertz": 16000,
                "languageCode": {"ko": "ko-KR", "en": "en-US"}.get(self.config.language, self.config.language),
                "model": self.config.model, "enableWordTimeOffsets": True,
                "enableAutomaticPunctuation": True},
                "audio": {"content": base64.b64encode(audio.pcm).decode("ascii")}},
        )
        return self.parse(data)
 
    @staticmethod
    def parse(data):
        if "error" in data:
            raise RuntimeError("Google STT가 오류를 반환했습니다.")
        out = []
        for result in data.get("results", []):
            alternatives = result.get("alternatives", [])
            if not alternatives:
                continue
            best = alternatives[0]
            if not best.get("transcript", "").strip():
                continue
            words = best.get("words", [])
            if not words:
                raise ValueError("Google 응답에 단어 시각이 없습니다.")
            out.append(Utterance(best["transcript"], float(words[0].get("startTime", "0s")[:-1]) * 1000,
                                 float(words[-1]["endTime"][:-1]) * 1000))
        return out
 
 
class ClovaSpeech:
    """CLOVA Speech long-form domain API (not legacy short-form CSR)."""
    def __init__(self, config):
        self.config = config
        self.url = required_env("CLOVA_SPEECH_INVOKE_URL").rstrip("/")
        parsed = urlparse(self.url)
        if parsed.scheme != "https" or parsed.hostname != "clovaspeech-gw.ncloud.com":
            raise ValueError("CLOVA Speech 콘솔의 HTTPS Invoke URL을 사용하세요.")
        self.key = required_env("CLOVA_SPEECH_SECRET")
        if config.model != "domain-default":
            raise ValueError("CLOVA의 model은 domain-default를 사용하세요. 실제 모델은 도메인에서 관리됩니다.")
 
    def transcribe(self, audio):
        params = {"language": {"ko": "ko-KR", "en": "en-US"}.get(self.config.language, self.config.language),
                  "completion": "sync", "wordAlignment": True, "fullText": True,
                  "diarization": {"enable": False}}
        data = request_json(self.url + "/recognizer/upload", timeout=self.config.timeout_seconds,
            headers={"X-CLOVASPEECH-API-KEY": self.key},
            files={"media": ("audio.wav", audio.wav_bytes(), "audio/wav"),
                   "params": (None, json.dumps(params), "application/json")})
        return self.parse(data)
 
    @staticmethod
    def parse(data):
        if data.get("result") != "COMPLETED":
            raise RuntimeError("CLOVA Speech 인식이 완료되지 않았습니다.")
        return [Utterance(s["text"], s["start"], s["end"]) for s in data.get("segments", [])]
 
 
PROVIDERS = {"faster-whisper": FasterWhisper, "google": GoogleSTT, "clova": ClovaSpeech}
 
 
def create_provider(config: STTConfig) -> Provider:
    if config.provider not in PROVIDERS:
        raise ValueError(f"지원하지 않는 STT: {config.provider}. 선택: {', '.join(PROVIDERS)}")
    return PROVIDERS[config.provider](config)
