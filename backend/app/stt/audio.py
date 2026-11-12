import io
import wave
from dataclasses import dataclass
from pathlib import Path
 
 
@dataclass(frozen=True)
class Audio:
    """Contract input: PCM signed 16-bit little endian, mono, 16 kHz."""
    pcm: bytes
 
    def __post_init__(self):
        if not self.pcm or len(self.pcm) % 2:
            raise ValueError("PCM은 비어 있지 않은 16-bit 샘플이어야 합니다.")
 
    @property
    def duration_ms(self) -> float:
        return len(self.pcm) / 32
 
    def wav_bytes(self) -> bytes:
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(self.pcm)
        return output.getvalue()
 
    @classmethod
    def from_wav(cls, path: str | Path):
        with wave.open(str(path), "rb") as wav:
            if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth(), wav.getcomptype()) != (16000, 1, 2, "NONE"):
                raise ValueError("16 kHz / mono / PCM 16-bit WAV가 필요합니다. README의 변환 방법을 참고하세요.")
            expected = wav.getnframes() * 2
            pcm = wav.readframes(wav.getnframes())
            if len(pcm) != expected:
                raise ValueError("WAV 파일이 잘렸습니다.")
            return cls(pcm)
