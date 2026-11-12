"""Replay a 16kHz mono int16 WAV in real time over the team's PCM wire format."""
import argparse
import asyncio
import json
from time import perf_counter

from websockets.asyncio.client import connect
from app.stt.audio import Audio


async def replay(audio, url):
    async with connect(url, max_size=1024 * 1024, open_timeout=30) as ws:
        await ws.send(json.dumps({"type": "session.start", "payload": {
            "encoding": "pcm_s16le", "sample_rate": 16000, "channels": 1}}))
        ready = json.loads(await asyncio.wait_for(ws.recv(), 180))
        if ready.get("type") != "session.ready":
            raise RuntimeError(ready)
        print(json.dumps(ready, ensure_ascii=False), flush=True)

        async def sender():
            started = perf_counter()
            for offset in range(0, len(audio.pcm), 3200):
                block = audio.pcm[offset:offset + 3200]
                # Mimic microphone arrival: send each 100ms after it would be captured.
                deadline = started + (offset + len(block)) / 32000
                await asyncio.sleep(max(0, deadline - perf_counter()))
                await ws.send(block)
            await ws.send(json.dumps({"type": "session.stop", "payload": {}}))

        task = asyncio.create_task(sender())
        try:
            async for message in ws:
                event = json.loads(message)
                if event.get("type") == "error":
                    raise RuntimeError(event["payload"])
                print(json.dumps(event, ensure_ascii=False), flush=True)
            await task
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WAV를 실제 시간 간격으로 WebSocket에 전송")
    parser.add_argument("wav")
    parser.add_argument("--url", default="ws://127.0.0.1:8000/api/stt/stream?model=tiny&chunk_seconds=2")
    args = parser.parse_args()
    asyncio.run(replay(Audio.from_wav(args.wav), args.url))
