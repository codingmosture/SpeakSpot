import threading
import unittest
from concurrent.futures import CancelledError
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from app.api.stt_app import app
from app.stt.audio import Audio
from app.stt.config import STTConfig
from app.stt.providers import Utterance, FasterWhisper
from types import SimpleNamespace
from app.stt.service import STTService
from app.stt.streaming import StreamingSTT
from app.stt.runtime import ModelRuntime, runtime
from app.api import stt_live

START = {"type": "session.start", "payload": {
    "encoding": "pcm_s16le", "sample_rate": 16000, "channels": 1}}
STOP = {"type": "session.stop", "payload": {}}


class Provider:
    def transcribe(self, audio):
        return [Utterance("테스트", 0, audio.duration_ms)] if any(audio.pcm) else []


class StreamingTests(unittest.TestCase):
    def test_team_schema_import_remains_compatible(self):
        from app.schemas.models import STTSegment as LegacySegment, Block, SearchResult
        from app.schemas.stt_models import STTSegment
        self.assertIs(LegacySegment, STTSegment)
        self.assertIn('bbox', Block.model_fields)
        self.assertIn('status', SearchResult.model_fields)

    def test_ten_minutes_of_input_keeps_clock_and_buffer_bounded(self):
        stream = StreamingSTT(STTService(STTConfig(), Provider()), 2)
        count = 0
        for _ in range(6000):
            count += len(stream.feed(bytes(3200)))
            self.assertLess(len(stream.chunks.pending), 64000)
        self.assertEqual(count, 300)
        stream.feed(b'\x01\x00' * 8000)
        segment = stream.finish()[0][0][0]
        self.assertEqual((segment.start_ms, segment.end_ms), (600000, 600500))

    def test_whisper_padding_is_clipped_to_actual_audio(self):
        provider = FasterWhisper.__new__(FasterWhisper)
        provider.config = STTConfig()
        raw = [SimpleNamespace(text='테스트', start=0.53, end=2.53),
               SimpleNamespace(text='outside padding', start=3, end=4)]
        provider.model = SimpleNamespace(transcribe=lambda *a, **kw: (iter(raw), None))
        result, _ = STTService(STTConfig(), provider).transcribe(Audio(bytes(64000)))
        self.assertEqual(len(result), 1)
        self.assertEqual((result[0].start_ms, result[0].end_ms), (530, 2000))

    def test_clock_ids_silence_and_tail(self):
        stream = StreamingSTT(STTService(STTConfig(), Provider()), 1)
        self.assertEqual(stream.feed(bytes(32000))[0][0], [])
        self.assertEqual(stream.feed(b'\x01\x00' * 4000), [])
        segment = stream.finish()[0][0][0]
        self.assertEqual((segment.start_ms, segment.end_ms), (1000, 1250))
        self.assertEqual(segment.segment_id, 'seg_000001')
        self.assertEqual(stream.finish(), [])
        with self.assertRaises(ValueError):
            stream.feed(bytes(2))

    def test_failed_stream_cannot_skip_audio_then_continue(self):
        provider = Provider()
        stream = StreamingSTT(STTService(STTConfig(), provider), 1)
        with patch.object(provider, 'transcribe', side_effect=RuntimeError('failure')):
            with self.assertRaises(RuntimeError):
                stream.feed(bytes(32000))
        with self.assertRaises(ValueError):
            stream.feed(bytes(32000))

    def test_cache_reuses_model_but_resets_session_ids(self):
        engine = ModelRuntime()
        with patch('app.stt.runtime.create_provider', return_value=Provider()) as factory:
            with engine.lock:
                first, hit1 = engine.create_service(STTConfig())
                second, hit2 = engine.create_service(STTConfig())
            self.assertFalse(hit1)
            self.assertTrue(hit2)
            self.assertIs(first.provider, second.provider)
            self.assertEqual(factory.call_count, 1)
            for service in (first, second):
                result, _ = service.transcribe(Audio(b'\x01\x00' * 8000))
                self.assertEqual(result[0].segment_id, 'seg_000001')

    def test_warmup_failure_does_not_poison_cache(self):
        engine = ModelRuntime()
        provider = Provider()
        with patch('app.stt.runtime.create_provider', return_value=provider):
            with patch.object(provider, 'transcribe', side_effect=RuntimeError('failure')):
                with self.assertRaises(RuntimeError):
                    engine.create_service(STTConfig())
            self.assertIsNone(engine.config)
            _, hit = engine.create_service(STTConfig())
            self.assertFalse(hit)


class WebSocketTests(unittest.TestCase):
    def setUp(self):
        self.patcher = patch.object(runtime, 'create_service', side_effect=lambda config:
            (STTService(config, Provider()), False))
        self.patcher.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.patcher.stop()
        self.assertFalse(runtime.lock.locked())

    def test_configured_frontend_origin(self):
        with patch.dict('os.environ', {'STT_ALLOWED_ORIGINS': 'http://localhost:5173'}):
            with self.client.websocket_connect('/api/stt/stream',
                    headers={'origin': 'http://localhost:5173'}) as ws:
                ws.send_json(START)
                self.assertEqual(ws.receive_json()['type'], 'session.ready')
                ws.send_json(STOP)
                self.assertEqual(ws.receive()['code'], 1000)

    def test_unlisted_frontend_origin_rejected(self):
        with patch.dict('os.environ', {'STT_ALLOWED_ORIGINS': ''}):
            with self.assertRaises(WebSocketDisconnect) as error:
                with self.client.websocket_connect('/api/stt/stream',
                        headers={'origin': 'https://unrelated.example'}):
                    pass
            self.assertEqual(error.exception.code, 1008)

    def test_results_during_stream_and_tail_before_close(self):
        with self.client.websocket_connect('/api/stt/stream?chunk_seconds=1') as ws:
            ws.send_json(START)
            self.assertEqual(ws.receive_json()['type'], 'session.ready')
            ws.send_bytes(b'\x01\x00' * 16000)
            result = ws.receive_json()
            self.assertEqual(result['type'], 'stt.result')
            self.assertEqual(result['payload']['end_ms'], 1000)
            ws.send_bytes(b'\x01\x00' * 8000)
            ws.send_json(STOP)
            tail = ws.receive_json()['payload']
            self.assertEqual((tail['start_ms'], tail['end_ms']), (1000, 1500))
            self.assertEqual(tail['segment_id'], 'seg_000002')
            self.assertEqual(ws.receive()['code'], 1000)

    def test_wrong_audio_format(self):
        with self.client.websocket_connect('/api/stt/stream') as ws:
            ws.send_json({'type': 'session.start', 'payload': {'sample_rate': 48000}})
            self.assertEqual(ws.receive_json()['payload']['code'], 'INVALID_FORMAT')
            ws.receive()

    def test_odd_bytes_and_oversized_frame(self):
        for data in (b'x', bytes(32002), b'RIFF' + bytes(20)):
            with self.client.websocket_connect('/api/stt/stream') as ws:
                ws.send_json(START)
                ws.receive_json()
                ws.send_bytes(data)
                self.assertEqual(ws.receive_json()['payload']['code'], 'INVALID_AUDIO')
                ws.receive()

    def test_busy_and_http_upload_share_same_lease(self):
        with runtime.lock:
            with self.client.websocket_connect('/api/stt/stream') as ws:
                self.assertEqual(ws.receive_json()['payload']['code'], 'STT_BUSY')
                ws.receive()
            response = self.client.post('/api/stt/transcribe',
                files={'file': ('silence.wav', Audio(bytes(32000)).wav_bytes(), 'audio/wav')})
            self.assertEqual(response.status_code, 409)

    def test_disconnect_releases_lease(self):
        try:
            with self.client.websocket_connect('/api/stt/stream') as ws:
                ws.send_json(START)
                ws.receive_json()
        except CancelledError:
            # TestClient may cancel the ASGI task when simulating an abrupt disconnect.
            # tearDown still requires the model lease to have been released.
            pass

    def test_backpressure_rejects_fast_sender(self):
        entered = threading.Event()
        release = threading.Event()
        class SlowProvider:
            def transcribe(self, audio):
                entered.set()
                release.wait(5)
                return []
        with patch.object(runtime, 'create_service', side_effect=lambda config:
                          (STTService(config, SlowProvider()), False)):
            with self.client.websocket_connect('/api/stt/stream?chunk_seconds=1') as ws:
                ws.send_json(START)
                ws.receive_json()
                ws.send_bytes(bytes(32000))
                self.assertTrue(entered.wait(2))
                for _ in range(11):
                    ws.send_bytes(bytes(32000))
                release.set()
                self.assertEqual(ws.receive_json()['payload']['code'], 'BACKPRESSURE')
                ws.receive()


class HTTPTests(unittest.TestCase):
    def test_session_mismatch_and_history_gap(self):
        previous = stt_live._current
        try:
            stt_live._current = stt_live.LiveSession(stt_live.StartRequest())
            for _ in range(160):
                stt_live._current.emit([], {}, 0)
            with TestClient(app) as client:
                self.assertTrue(client.get('/api/stt/live/results').json()['history_truncated'])
                self.assertEqual(client.post('/api/stt/live/stop?session_id=old').status_code, 409)
                self.assertEqual(client.get('/api/stt/live/results?session_id=old').status_code, 409)
                stt_live._current = None  # No worker was started in this unit fixture.
        finally:
            stt_live._current = previous


if __name__ == '__main__':
    unittest.main()
