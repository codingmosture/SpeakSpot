"""Microphone -> bounded queue -> STTService -> terminal, without a web server."""
import argparse
import json
import math
import queue
import signal
import sys
import threading
from time import perf_counter

from .audio import Audio
from .config import STTConfig
from .providers import PROVIDERS
from .service import STTService


class PCMChunks:
    """Keep every sample, including the short remainder on shutdown."""

    def __init__(self, seconds=2.0):
        if not math.isfinite(seconds) or not 0.5 <= seconds <= 10:
            raise ValueError("chunk-seconds는 0.5~10초이어야 합니다.")
        self.size = round(seconds * 16000) * 2
        self.pending = bytearray()

    def feed(self, pcm):
        if len(pcm) % 2:
            raise ValueError("16-bit PCM 샘플이 잘렸습니다.")
        self.pending.extend(pcm)
        while len(self.pending) >= self.size:
            chunk = bytes(self.pending[:self.size])
            del self.pending[:self.size]
            yield Audio(chunk)

    def finish(self):
        if self.pending:
            audio = Audio(bytes(self.pending))
            self.pending.clear()
            return audio
        return None


class MicrophoneBuffer:
    """Callback only copies PCM; the main thread does all inference and printing."""

    def __init__(self, max_blocks=30):
        self.blocks = queue.Queue(maxsize=max_blocks)
        self.stop = threading.Event()
        self.error = None

    def callback(self, data, frames, timing, status):
        if self.stop.is_set():
            return
        if status:
            # Do not silently continue with an inaccurate session audio clock.
            self.error = "마이크 입력 손실이 감지됐습니다. 입력장치와 PC 부하를 확인하세요."
            self.stop.set()
            return
        try:
            self.blocks.put_nowait((bytes(data), perf_counter()))
        except queue.Full:
            self.error = "인식이 녹음보다 느려 대기열이 찼습니다. tiny 모델 또는 더 긴 청크로 재실행하세요."
            self.stop.set()


def consume(blocks, chunks, service, emit, last_capture=None):
    """Consume a stopped recording, preserving the final short chunk."""
    if last_capture is None:
        last_capture = perf_counter()
    while True:
        try:
            pcm, last_capture = blocks.get_nowait()
        except queue.Empty:
            break
        for audio in chunks.feed(pcm):
            emit(*service.transcribe(audio), last_capture)
    tail = chunks.finish()
    if tail is not None:
        emit(*service.transcribe(tail), last_capture)


def print_result(segments, metrics, captured_at, *, json_output=False):
    for segment in segments:
        if json_output:
            print(json.dumps({"type": "stt.result", "payload": segment.model_dump()},
                             ensure_ascii=False), flush=True)
        else:
            print(f"[{segment.start_ms / 1000:7.2f}~{segment.end_ms / 1000:7.2f}초] "
                  f"{segment.text}", flush=True)
    # Approximate delay from the last microphone callback of this chunk, not word latency.
    lag_ms = max(0, (perf_counter() - captured_at) * 1000)
    print(f"처리 {metrics['processing_ms']:.0f} ms | RTF {metrics['rtf']:.2f} | "
          f"청크 끝 이후 지연 약 {lag_ms:.0f} ms"
          + (" | 인식된 말 없음" if not segments else ""), file=sys.stderr, flush=True)


def parser():
    p = argparse.ArgumentParser(description="프론트엔드 없이 마이크 연속 STT")
    p.add_argument("--provider", choices=list(PROVIDERS), default="faster-whisper")
    p.add_argument("--model", help="기본 tiny / Google default / CLOVA domain-default")
    p.add_argument("--chunk-seconds", type=float, default=2.0)
    p.add_argument("--input-device", type=int)
    p.add_argument("--list-devices", action="store_true")
    p.add_argument("--seconds", type=float, default=0, help="0이면 Ctrl+C까지 계속 녹음")
    p.add_argument("--language", default="ko")
    p.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    p.add_argument("--compute-type", default="int8")
    p.add_argument("--beam-size", type=int, default=1)
    p.add_argument("--json", action="store_true", help="stdout에 팀 규격 JSON 이벤트 출력")
    return p


def run(args):
    import sounddevice as sd

    if args.list_devices:
        print(sd.query_devices())
        return
    chunks = PCMChunks(args.chunk_seconds)
    if not math.isfinite(args.seconds) or args.seconds < 0:
        raise ValueError("seconds는 0 이상의 유한한 수여야 합니다.")
    sd.check_input_settings(device=args.input_device, channels=1,
                            dtype="int16", samplerate=16000)
    config = STTConfig(provider=args.provider,
        model=args.model or {"faster-whisper": "tiny", "google": "default",
                            "clova": "domain-default"}[args.provider],
        language=args.language, device=args.device, compute_type=args.compute_type,
        beam_size=args.beam_size)
    print("모델 준비 중… 첫 실행은 다운로드가 필요합니다.", file=sys.stderr, flush=True)
    service = STTService(config)
    # Warm up BEFORE opening the microphone, so initial inference cannot fill the queue.
    service.provider.transcribe(Audio(bytes(32000)))
    capture = MicrophoneBuffer()
    emit = lambda segments, metrics, captured: print_result(
        segments, metrics, captured, json_output=args.json)
    previous_handler = signal.getsignal(signal.SIGINT)
    # Ctrl+C requests a graceful stop rather than interrupting an active inference.
    signal.signal(signal.SIGINT, lambda *_: capture.stop.set())
    timer = None
    captured_at = perf_counter()
    try:
        with sd.RawInputStream(samplerate=16000, blocksize=1600, channels=1,
                               dtype="int16", device=args.input_device,
                               callback=capture.callback) as stream:
            if args.seconds:
                timer = threading.Timer(args.seconds, capture.stop.set)
                timer.daemon = True
                timer.start()
            print("마이크 ON — 말씀하세요. 종료: Ctrl+C (남은 음성 처리 후 종료)",
                  file=sys.stderr, flush=True)
            while not capture.stop.is_set():
                try:
                    pcm, captured_at = capture.blocks.get(timeout=0.1)
                except queue.Empty:
                    if not stream.active:
                        raise RuntimeError("마이크가 연결 해제되었거나 입력 스트림이 종료됐습니다.")
                    continue
                for audio in chunks.feed(pcm):
                    emit(*service.transcribe(audio), captured_at)
        # The stream is closed now: the callback can no longer append audio.
        print("마이크 OFF — 대기 중인 음성을 마저 처리합니다.", file=sys.stderr, flush=True)
        consume(capture.blocks, chunks, service, emit, captured_at)
        if capture.error:
            raise RuntimeError(capture.error)
    finally:
        if timer is not None:
            timer.cancel()
        signal.signal(signal.SIGINT, previous_handler)


def main():
    args = parser().parse_args()
    try:
        run(args)
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        # Provider adapters already sanitize remote request errors and credentials.
        print(f"실행 실패: {error}", file=sys.stderr)
        print("설치: python -m pip install -r app/stt/requirements/requirements-stt-live.txt\n"
              "장치 확인: python -m app.stt.live --list-devices", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
