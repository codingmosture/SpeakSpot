"""Microphone capture and cloud response consumption run concurrently."""
import queue
import threading
from time import monotonic

from .cloud_stream import CloudSetupError, create_cloud_stream, safe_cloud_error


def run_cloud_microphone(settings, capture, on_state, on_segment, on_audio):
    import sounddevice as sd
    adapter = create_cloud_stream(settings)
    done = threading.Event()
    failures = []
    timer = None

    def audio():
        while True:
            try:
                pcm, captured_at = capture.blocks.get(timeout=.1)
            except queue.Empty:
                if capture.stop.is_set():
                    return
                continue
            on_audio(len(pcm) / 32)
            yield pcm

    def receive():
        try:
            adapter.run(audio(), on_segment)
            if not capture.stop.is_set():
                failures.append('클라우드가 녹음 도중 연결을 종료했습니다. 새 테스트를 시작하세요.')
        except Exception as error:
            failures.append(safe_cloud_error(error))
        finally:
            done.set()
            capture.stop.set()

    worker = threading.Thread(target=receive, daemon=True)
    try:
        worker.start()
        deadline = monotonic() + 15
        while not adapter.ready.wait(.05):
            if done.is_set() or capture.stop.is_set():
                break
            if monotonic() > deadline:
                raise CloudSetupError('클라우드 연결 준비 시간이 초과되었습니다.')
        if not capture.stop.is_set():
            with sd.RawInputStream(samplerate=16000, blocksize=1600, channels=1,
                                  dtype='int16', device=settings.input_device, callback=capture.callback) as stream:
                on_state('recording')
                timer = threading.Timer(settings.duration_seconds, capture.stop.set)
                timer.daemon = True
                timer.start()
                while not capture.stop.wait(.1):
                    if not stream.active:
                        raise CloudSetupError('마이크 연결이 끊어졌습니다.')
        on_state('stopping')
        if not done.wait(15):
            raise CloudSetupError('마지막 클라우드 응답 대기 시간이 초과되었습니다. 일부 결과가 누락될 수 있습니다.')
        if failures:
            raise CloudSetupError(failures[0])
        if capture.error:
            raise CloudSetupError(capture.error)
    finally:
        capture.stop.set()
        if timer:
            timer.cancel()
        adapter.close()
        if worker.ident is not None:
            worker.join(timeout=5)
