from time import perf_counter
from faster_whisper import WhisperModel
 
import argparse
parser = argparse.ArgumentParser()
parser.add_argument("wav")
args = parser.parse_args()

started = perf_counter()
model = WhisperModel("tiny", device="cpu", compute_type="int8")
print("model load seconds:", perf_counter() - started)
 
started = perf_counter()
segments, info = model.transcribe(
    args.wav, language="ko", vad_filter=True
)
segments = list(segments)
print("processing seconds:", perf_counter() - started)
for segment in segments:
    print(segment.start, segment.end, segment.text)
