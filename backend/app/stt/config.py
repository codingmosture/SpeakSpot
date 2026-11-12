from dataclasses import dataclass
 
 
@dataclass(frozen=True)
class STTConfig:
    provider: str = "faster-whisper"
    model: str = "small"
    language: str = "ko"
    device: str = "cpu"
    compute_type: str = "int8"
    beam_size: int = 5
    vad_filter: bool = True
    timeout_seconds: float = 120
 
    def __post_init__(self):
        if self.beam_size < 1 or not 0 < self.timeout_seconds <= 600:
            raise ValueError("beam_size >= 1, timeout_seconds는 0 초과 600 이하이어야 합니다.")
