"""Transport-independent PCM stream: usable from the team's own session router."""
from .live import PCMChunks


class StreamingSTT:
    def __init__(self, service, chunk_seconds=2):
        self.service = service
        self.chunks = PCMChunks(chunk_seconds)
        self.closed = False
        self.failed = False
        self.received_bytes = 0

    def feed(self, pcm: bytes):
        if self.closed or self.failed:
            raise ValueError("종료되었거나 실패한 STT 스트림입니다. 새 세션을 만드세요.")
        if not pcm or len(pcm) % 2:
            raise ValueError("비어 있지 않은 16-bit PCM이 필요합니다.")
        self.received_bytes += len(pcm)
        try:
            return [self.service.transcribe(audio) for audio in self.chunks.feed(pcm)]
        except Exception:
            self.failed = True
            raise

    def finish(self):
        if self.failed:
            raise ValueError("실패한 STT 스트림은 이어서 처리할 수 없습니다.")
        if self.closed:
            return []
        self.closed = True
        tail = self.chunks.finish()
        return [self.service.transcribe(tail)] if tail is not None else []
