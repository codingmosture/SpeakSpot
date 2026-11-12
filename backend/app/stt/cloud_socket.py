"""Bounded bridge between asynchronous WebSocket PCM and synchronous cloud SDKs."""
import asyncio
import queue
import threading
from time import monotonic

from .cloud_stream import CloudSetupError, create_cloud_stream, safe_cloud_error


class CloudSocketBridge:
    def __init__(self, settings):
        self.adapter = create_cloud_stream(settings)
        self.audio = queue.Queue(maxsize=128)
        self.results = queue.Queue(maxsize=128)
        self.abort = threading.Event()
        self.done = threading.Event()
        self.input_finished = threading.Event()
        self.lock = threading.Lock()
        self.pending_bytes = 0
        self.failure = None
        self.worker = threading.Thread(target=self._run, daemon=True)

    def feed(self, pcm):
        with self.lock:
            if self.pending_bytes + len(pcm) > 320000:
                raise CloudSetupError('클라우드 음성 전송 대기열이 10초를 초과했습니다.')
            try:
                self.audio.put_nowait(pcm)
            except queue.Full:
                raise CloudSetupError('클라우드 음성 메시지 대기열이 가득 찼습니다.') from None
            self.pending_bytes += len(pcm)

    def finish(self):
        self.input_finished.set()

    def _pcm(self):
        while not self.abort.is_set():
            try:
                block = self.audio.get(timeout=.05)
            except queue.Empty:
                if self.input_finished.is_set():
                    return
                continue
            # SDKs receive at most 100ms per request, regardless of browser frames.
            try:
                for offset in range(0, len(block), 3200):
                    if self.abort.is_set():
                        return
                    yield block[offset:offset + 3200]
            finally:
                with self.lock:
                    self.pending_bytes -= len(block)

    def _emit(self, segment):
        deadline = monotonic() + 10
        while not self.abort.is_set():
            try:
                self.results.put(segment, timeout=.05)
                return
            except queue.Full:
                if monotonic() >= deadline:
                    raise CloudSetupError('클라이언트가 결과를 받지 못해 전송을 중단했습니다.')

    def _run(self):
        try:
            self.adapter.run(self._pcm(), self._emit)
            if not self.input_finished.is_set() and not self.abort.is_set():
                raise CloudSetupError('클라우드가 음성 수신 도중 종료되었습니다. 새 세션을 시작하세요.')
            with self.lock:
                if self.pending_bytes and not self.abort.is_set():
                    raise CloudSetupError('클라우드가 모든 음성을 처리하기 전에 종료되었습니다.')
        except Exception as error:
            self.failure = safe_cloud_error(error)
        finally:
            self.done.set()

    async def wait_ready(self):
        deadline = monotonic() + 15
        while not self.adapter.ready.is_set():
            self.check_failure()
            if self.done.is_set():
                raise CloudSetupError('클라우드가 준비 전에 종료되었습니다.')
            if monotonic() >= deadline:
                raise CloudSetupError('클라우드 연결 준비 시간이 초과되었습니다.')
            await asyncio.sleep(.01)
        self.check_failure()

    def check_failure(self):
        if self.failure:
            raise CloudSetupError(self.failure)

    def close(self):
        self.abort.set()
        try:
            self.adapter.close()
        finally:
            if self.worker.ident is not None:
                self.worker.join(timeout=5)
                if self.worker.is_alive():
                    raise CloudSetupError('클라우드 연결 종료가 지연됐습니다. 서버를 재시작하세요.')
