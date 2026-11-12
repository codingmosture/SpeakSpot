"""Cloud socket contract tests without credentials, microphone, or paid API calls."""
import threading
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from app.api.stt_app import app
from app.schemas.stt_models import STTSegment, StartRequest
from app.stt.cloud_socket import CloudSocketBridge
from app.stt.cloud_stream import CloudSetupError
from app.stt.runtime import runtime

START = {'type': 'session.start', 'payload': {
    'encoding': 'pcm_s16le', 'sample_rate': 16000, 'channels': 1}}
STOP = {'type': 'session.stop', 'payload': {}}


class Adapter:
    def __init__(self):
        self.ready = threading.Event()
        self.closed = threading.Event()
        self.blocks = []

    def run(self, pcm, emit):
        self.ready.set()
        for block in pcm:
            self.blocks.append(block)
            emit(STTSegment(segment_id='seg_000001', text='중간', start_ms=0,
                            end_ms=100, is_final=False))
        if self.blocks:
            emit(STTSegment(segment_id='seg_000001', text='최종 결과', start_ms=0,
                            end_ms=sum(map(len, self.blocks)) // 32, is_final=True))

    def close(self):
        self.closed.set()


class CloudSocketTests(unittest.TestCase):
    def setUp(self):
        self.adapter = Adapter()
        self.factory = patch('app.stt.cloud_socket.create_cloud_stream', return_value=self.adapter)
        self.mock_factory = self.factory.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.factory.stop()
        self.assertFalse(runtime.lock.locked())

    def test_both_providers_partial_final_and_tail(self):
        for provider in ('google', 'clova'):
            self.adapter = Adapter()
            self.mock_factory.return_value = self.adapter
            with self.client.websocket_connect('/api/stt/stream?provider=' + provider) as ws:
                ws.send_json(START)
                self.assertEqual(ws.receive_json()['payload']['provider'], provider)
                ws.send_bytes(bytes(4800))
                self.assertFalse(ws.receive_json()['payload']['is_final'])
                ws.send_json(STOP)
                events = []
                while True:
                    message = ws.receive()
                    if message['type'] == 'websocket.close':
                        self.assertEqual(message['code'], 1000)
                        break
                    import json
                    events.append(json.loads(message['text']))
                final = events[-1]['payload']
                self.assertTrue(final['is_final'])
                self.assertEqual(final['segment_id'], 'seg_000001')
                self.assertEqual(final['end_ms'], 150)
            self.assertEqual(list(map(len, self.adapter.blocks)), [3200, 1600])
            self.assertTrue(self.adapter.closed.is_set())

    def test_invalid_provider_model_language_and_duration(self):
        for query in ('provider=other', 'provider=google&model=tiny',
                      'provider=clova&language=xx', 'provider=google&duration_seconds=241',
                      'provider=clova&chunk_seconds=nan'):
            with self.client.websocket_connect('/api/stt/stream?' + query) as ws:
                self.assertEqual(ws.receive_json()['payload']['code'], 'INVALID_CONFIG')
                ws.receive()
        self.mock_factory.assert_not_called()

    def test_invalid_audio_closes_adapter(self):
        with self.client.websocket_connect('/api/stt/stream?provider=clova') as ws:
            ws.send_json(START)
            ws.receive_json()
            ws.send_bytes(b'RIFF' + bytes(20))
            self.assertEqual(ws.receive_json()['payload']['code'], 'INVALID_AUDIO')
            ws.receive()
        self.assertTrue(self.adapter.closed.is_set())

    def test_disconnect_closes_worker(self):
        with self.client.websocket_connect('/api/stt/stream?provider=google') as ws:
            ws.send_json(START)
            ws.receive_json()
            ws.close()
        self.assertTrue(self.adapter.closed.wait(2))

    def test_cloud_failure_is_sanitized(self):
        def fail(pcm, emit):
            raise RuntimeError('secret-token-do-not-expose')
        self.adapter.run = fail
        with self.client.websocket_connect('/api/stt/stream?provider=google') as ws:
            ws.send_json(START)
            error = ws.receive_json()
            self.assertEqual(error['payload']['code'], 'CLOUD_FAILED')
            self.assertNotIn('secret-token', str(error))
            ws.receive()
        self.assertTrue(self.adapter.closed.is_set())

    def test_duration_limit_drains_and_closes(self):
        with self.client.websocket_connect('/api/stt/stream?provider=google&duration_seconds=1') as ws:
            ws.send_json(START)
            ws.receive_json()
            ws.send_bytes(bytes(3200))
            self.assertFalse(ws.receive_json()['payload']['is_final'])
            self.assertTrue(ws.receive_json()['payload']['is_final'])
            self.assertEqual(ws.receive()['code'], 1000)

    def test_audio_duration_limit(self):
        with self.client.websocket_connect('/api/stt/stream?provider=clova&duration_seconds=1') as ws:
            ws.send_json(START)
            ws.receive_json()
            ws.send_bytes(bytes(32000))
            ws.send_bytes(bytes(32000))
            while True:
                event = ws.receive_json()
                if event['type'] == 'error':
                    self.assertEqual(event['payload']['code'], 'SESSION_LIMIT')
                    break
            ws.receive()

    def test_input_backpressure_is_bounded(self):
        bridge = CloudSocketBridge(StartRequest(provider='clova'))
        try:
            for _ in range(10):
                bridge.feed(bytes(32000))
            with self.assertRaises(CloudSetupError):
                bridge.feed(bytes(2))
            self.assertEqual(bridge.pending_bytes, 320000)
        finally:
            bridge.close()

    def test_empty_stop_closes_cleanly(self):
        with self.client.websocket_connect('/api/stt/stream?provider=clova') as ws:
            ws.send_json(START)
            ws.receive_json()
            ws.send_json(STOP)
            self.assertEqual(ws.receive()['code'], 1000)
        self.assertEqual(self.adapter.blocks, [])

    def test_early_cloud_eof_is_error(self):
        def early(pcm, emit):
            self.adapter.ready.set()
        self.adapter.run = early
        with self.client.websocket_connect('/api/stt/stream?provider=clova') as ws:
            ws.send_json(START)
            event = ws.receive_json()
            if event['type'] == 'session.ready':
                event = ws.receive_json()
            self.assertEqual(event['payload']['code'], 'CLOUD_FAILED')
            self.assertEqual(ws.receive()['code'], 1011)

    def test_bad_handshake_does_not_open_cloud(self):
        with self.client.websocket_connect('/api/stt/stream?provider=google') as ws:
            ws.send_json({'type': 'session.start', 'payload': {'sample_rate': 48000}})
            self.assertEqual(ws.receive_json()['payload']['code'], 'INVALID_FORMAT')
            ws.receive()
        self.mock_factory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
