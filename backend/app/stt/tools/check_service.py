from app.stt import STTConfig, STTService
from app.stt.audio import Audio
 
import argparse
parser = argparse.ArgumentParser()
parser.add_argument("wav")
args = parser.parse_args()
audio = Audio.from_wav(args.wav)
service = STTService(STTConfig(model="tiny"))
segments, metrics = service.transcribe(audio)
print([s.model_dump() for s in segments])
print(metrics)
