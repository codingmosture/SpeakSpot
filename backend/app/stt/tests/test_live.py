import queue
import unittest

from app.stt.audio import Audio
from app.stt.config import STTConfig
from app.stt.live import MicrophoneBuffer, PCMChunks, consume
from app.stt.providers import Utterance
from app.stt.service import STTService


class FakeProvider:
    def __init__(self):
        self.received = []

    def transcribe(self, audio):
        self.received.append(audio.pcm)
        if not any(audio.pcm):
            return []
        return [Utterance("테스트", 0, audio.duration_ms)]


class LiveTests(unittest.TestCase):
    def test_arbitrary_blocks_preserve_all_samples_and_tail(self):
        source = bytes(range(256)) * 625  # 5 seconds
        chunks = PCMChunks(2)
        result = []
        for start in range(0, len(source), 600):
            result.extend(chunks.feed(source[start:start + 600]))
        result.append(chunks.finish())
        self.assertEqual([a.duration_ms for a in result], [2000, 2000, 1000])
        self.assertEqual(b"".join(a.pcm for a in result), source)
        self.assertIsNone(chunks.finish())

    def test_stop_drains_queue_and_partial_chunk_once(self):
        chunks = PCMChunks(2)
        list(chunks.feed(bytes(16000)))  # 0.5 seconds already consumed
        blocks = queue.Queue()
        blocks.put((bytes(48000), 1.0))  # remaining silence until 2s
        blocks.put((b"\x01\x00" * 8000, 2.0))  # final 0.5 seconds
        provider = FakeProvider()
        service = STTService(STTConfig(), provider)
        results = []
        consume(blocks, chunks, service, lambda *args: results.append(args))
        segment = results[1][0][0]
        self.assertEqual((segment.start_ms, segment.end_ms), (2000, 2500))
        self.assertEqual(segment.segment_id, "seg_000001")
        self.assertEqual(len(provider.received), 2)
        self.assertEqual(results[1][2], 2.0)
        consume(blocks, chunks, service, lambda *args: results.append(args))
        self.assertEqual(len(provider.received), 2)

    def test_callback_copies_reused_input_buffer(self):
        capture = MicrophoneBuffer()
        source = bytearray(b"\x01\x00")
        capture.callback(source, 1, None, False)
        source[0] = 2
        self.assertEqual(capture.blocks.get_nowait()[0], b"\x01\x00")

    def test_full_queue_stops_without_overwriting_existing_audio(self):
        capture = MicrophoneBuffer(max_blocks=1)
        capture.callback(b"\x01\x00", 1, None, False)
        capture.callback(b"\x02\x00", 1, None, False)
        self.assertTrue(capture.stop.is_set())
        self.assertIsNotNone(capture.error)
        self.assertEqual(capture.blocks.get_nowait()[0], b"\x01\x00")
        capture.callback(b"\x03\x00", 1, None, False)
        self.assertTrue(capture.blocks.empty())

    def test_device_overflow_is_reported_and_stops(self):
        capture = MicrophoneBuffer()
        capture.callback(bytes(3200), 1600, None, True)
        self.assertTrue(capture.stop.is_set())
        self.assertIsNotNone(capture.error)
        self.assertTrue(capture.blocks.empty())

    def test_bad_chunk_duration_rejected(self):
        for value in (0, -1, 11, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                PCMChunks(value)


if __name__ == "__main__":
    unittest.main()
