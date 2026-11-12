import unittest
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

from app.api import stt_live as api
from app.stt.providers import Utterance
from app.stt.service import STTService
from app.stt.config import STTConfig
from app.stt.audio import Audio


class LiveAPITests(unittest.TestCase):
    def setUp(self):
        self.previous = api._current
        api._current = None

    def tearDown(self):
        api._current = self.previous

    def test_start_poll_stop_and_duplicate_start(self):
        with patch.object(api.LiveSession, 'work', autospec=True) as work:
            started = api.start(api.StartRequest())
            api._current.thread.join(2)
            self.assertEqual(started['settings']['duration_seconds'], 30)
            with patch.object(api._current.thread, 'is_alive', return_value=True):
                with self.assertRaises(HTTPException) as error:
                    api.start(api.StartRequest())
                self.assertEqual(error.exception.status_code, 409)
            api.stop()
            self.assertTrue(api._current.capture.stop.is_set())
            self.assertEqual(api.results()['state'], 'stopping')
            work.assert_called_once()

    def test_results_keep_contract_and_cursor(self):
        session = api.LiveSession(api.StartRequest())
        api._current = session
        class Provider:
            def transcribe(self, audio):
                return [Utterance('안녕하세요', 0, 1000)]
        service = STTService(STTConfig(), Provider())
        for _ in range(2):
            session.emit(*service.transcribe(Audio(bytes(32000))), 0)
        result = api.results(1)
        self.assertEqual(len(result['results']), 1)
        segment = result['results'][0]['segments'][0]
        self.assertEqual(segment['start_ms'], 1000)
        self.assertEqual(segment['segment_id'], 'seg_000002')
        self.assertTrue(segment['is_final'])
        result['results'][0]['segments'].clear()
        self.assertEqual(len(api.results(1)['results'][0]['segments']), 1)

    def test_history_bounded(self):
        session = api.LiveSession(api.StartRequest())
        for _ in range(160):
            session.emit([], {}, 0)
        snapshot = session.snapshot()
        self.assertEqual(len(snapshot['results']), 150)
        self.assertEqual(snapshot['oldest_chunk_id'], 11)
        self.assertEqual(snapshot['last_chunk_id'], 160)

    def test_missing_session_and_invalid_settings(self):
        for action in (api.results, api.stop):
            with self.assertRaises(HTTPException) as error:
                action()
            self.assertEqual(error.exception.status_code, 404)
        for settings in ({'duration_seconds': 0}, {'duration_seconds': 1801},
                         {'chunk_seconds': float('nan')}, {'model': 'unknown'}):
            with self.assertRaises(ValidationError):
                api.StartRequest(**settings)

    def test_worker_failure_is_visible(self):
        session = api.LiveSession(api.StartRequest())
        with patch('sounddevice.check_input_settings', side_effect=RuntimeError('device unavailable')):
            session.work()
        self.assertEqual(session.snapshot()['state'], 'error')
        self.assertIn('device unavailable', session.snapshot()['error'])


if __name__ == '__main__':
    unittest.main()
