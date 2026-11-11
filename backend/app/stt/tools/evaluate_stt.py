"""Reproducible chunked-file benchmark with optional Korean character error rate."""
import argparse
import hashlib
import importlib.metadata
import json
import platform
import unicodedata
from pathlib import Path
from time import perf_counter

from app.stt import STTConfig, STTService
from app.stt.audio import Audio
from app.stt.providers import create_provider
from app.stt.streaming import StreamingSTT


def normalize(text):
    # Hangul syllable normalization; ignore whitespace, retain punctuation/case.
    return ''.join(unicodedata.normalize('NFC', text).split())


def character_error_rate(reference, hypothesis):
    reference, hypothesis = normalize(reference), normalize(hypothesis)
    if not reference:
        raise ValueError("정답 텍스트는 비어 있을 수 없습니다.")
    previous = list(range(len(hypothesis) + 1))
    for i, a in enumerate(reference, 1):
        current = [i]
        for j, b in enumerate(hypothesis, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (a != b)))
        previous = current
    return previous[-1] / len(reference)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('wav', type=Path)
    p.add_argument('--reference', type=Path, help='UTF-8 정답 텍스트; 없으면 CER 계산 생략')
    p.add_argument('--provider', choices=['faster-whisper', 'google', 'clova'], default='faster-whisper')
    p.add_argument('--model')
    p.add_argument('--chunk-seconds', type=float, default=2)
    p.add_argument('--runs', type=int, default=3)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if not 1 <= args.runs <= 20 or not 0.5 <= args.chunk_seconds <= 10:
        p.error('runs=1~20, chunk-seconds=0.5~10이어야 합니다.')
    if args.output.exists():
        p.error('결과 파일이 이미 있습니다. 새로운 이름을 사용하세요.')
    audio = Audio.from_wav(args.wav)
    reference = args.reference.read_text(encoding='utf-8-sig') if args.reference else None
    if reference is not None and not normalize(reference):
        p.error('정답 파일이 비어 있습니다.')
    config = STTConfig(provider=args.provider, model=args.model or {
        'faster-whisper': 'tiny', 'google': 'default', 'clova': 'domain-default'}[args.provider], beam_size=1)
    load_start = perf_counter()
    provider = create_provider(config)
    provider.transcribe(Audio(bytes(32000)))
    initialization_ms = (perf_counter() - load_start) * 1000
    trials = []
    for trial in range(args.runs):
        stream = StreamingSTT(STTService(config, provider), args.chunk_seconds)
        batches = []
        for offset in range(0, len(audio.pcm), 3200):
            batches.extend(stream.feed(audio.pcm[offset:offset + 3200]))
        batches.extend(stream.finish())
        segments = [s.model_dump() for result, _ in batches for s in result]
        text = ' '.join(s['text'] for s in segments)
        processing_ms = sum(metrics['processing_ms'] for _, metrics in batches)
        row = {'run': trial + 1, 'text': text, 'segments': segments,
               'processing_ms': processing_ms, 'rtf': processing_ms / audio.duration_ms,
               'cer': character_error_rate(reference, text) if reference is not None else None}
        trials.append(row)
        print(f"run={trial + 1} RTF={row['rtf']:.3f} CER={row['cer']}")
    report = {'provider': config.provider, 'model': config.model,
        'chunk_seconds': args.chunk_seconds, 'audio_ms': audio.duration_ms,
        'pcm_sha256': hashlib.sha256(audio.pcm).hexdigest(),
        'initialization_and_warmup_ms': initialization_ms,
        'normalization': 'NFC, whitespace removed; punctuation and case retained',
        'python': platform.python_version(), 'platform': platform.platform(),
        'faster_whisper': importlib.metadata.version('faster-whisper'), 'runs': trials}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as output:
        json.dump(report, output, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
