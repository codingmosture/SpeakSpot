import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import grpc
from google.cloud import speech_v1 as speech
from app.schemas.stt_models import StartRequest, STTSegment
from app.stt.cloud_stream import GoogleResults, GoogleStreaming, ClovaResults, ClovaStreaming, safe_cloud_error, CloudSetupError
from app.stt.cloud_microphone import run_cloud_microphone
from app.stt.live import MicrophoneBuffer
from app.stt.proto import nest_pb2 as nest
from app.api.stt_live import LiveSession


def google_result(text, end, final):
    return speech.StreamingRecognizeResponse(results=[speech.StreamingRecognitionResult(
        alternatives=[speech.SpeechRecognitionAlternative(transcript=text)],
        result_end_time=timedelta(milliseconds=end), is_final=final)])


class CloudTests(unittest.TestCase):
    def test_provider_defaults_and_limits(self):
        self.assertEqual(StartRequest(provider='google').model, 'default')
        self.assertEqual(StartRequest(provider='clova').model, 'domain-default')
        self.assertEqual(StartRequest().model, 'tiny')
        for kwargs in ({'provider': 'google', 'model': 'tiny'},
                       {'provider': 'google', 'duration_seconds': 241},
                       {'provider': 'clova', 'secret': 'do-not-put-keys-in-body'}):
            with self.assertRaises(ValueError):
                StartRequest(**kwargs)

    def test_google_interim_and_final_have_same_id(self):
        mapper = GoogleResults()
        a = mapper.parse(google_result('안녕', 700, False))[0]
        b = mapper.parse(google_result('안녕하세요', 1000, False))[0]
        c = mapper.parse(google_result('안녕하세요.', 1200, True))[0]
        d = mapper.parse(google_result('다음', 1800, False))[0]
        self.assertEqual({a.segment_id, b.segment_id, c.segment_id}, {'seg_000001'})
        self.assertTrue(c.is_final)
        self.assertFalse(b.is_final)
        self.assertEqual(d.segment_id, 'seg_000002')
        self.assertEqual(d.start_ms, 1200)

    def test_google_uses_native_sdk_requests(self):
        settings = StartRequest(provider='google')
        observed = {}
        class Client:
            transport = SimpleNamespace(close=lambda: None)
            def __init__(self, **kwargs): pass
            def streaming_recognize(self, config, requests, **kwargs):
                observed['config'] = config
                observed['audio'] = [r.audio_content for r in requests]
                observed.update(kwargs)
                return iter([google_result('음성', 100, True)])
        with (patch('google.auth.default', return_value=(object(), 'project')),
             patch('google.cloud.speech_v1.SpeechClient', Client),
             patch.dict('os.environ', {'GOOGLE_ACCESS_TOKEN': ''})):
            adapter = GoogleStreaming(settings)
            output = []
            adapter.run(iter([bytes(3200), bytes(3200)]), output.append)
            adapter.close()
        self.assertTrue(observed['config'].interim_results)
        self.assertEqual(observed['config'].config.sample_rate_hertz, 16000)
        self.assertEqual(observed['config'].config.language_code, 'ko-KR')
        self.assertEqual(len(observed['audio']), 2)
        self.assertIsNone(observed['retry'])
        self.assertEqual(len(output), 1)

    def test_clova_response_and_deduplication(self):
        mapper = ClovaResults()
        response = {'transcription': {'text': '안녕하세요', 'position': 0,
                    'startTimestamp': 100, 'endTimestamp': 900}}
        segment = mapper.parse(response)[0]
        self.assertEqual((segment.start_ms, segment.end_ms), (100, 900))
        self.assertTrue(segment.is_final)
        self.assertEqual(mapper.parse(response), [])
        with self.assertRaises(CloudSetupError):
            mapper.parse({'config': {'status': 'Not Authorized'}})

    def test_clova_real_grpc_wire_and_final_flush(self):
        received = []
        def recognize(requests, context):
            first = next(requests)
            received.append(first)
            self.assertEqual(first.type, nest.CONFIG)
            self.assertEqual(dict(context.invocation_metadata())['authorization'], 'Bearer test-only')
            yield nest.NestResponse(contents=json.dumps({'config': {'status': 'Success'}}))
            for request in requests:
                received.append(request)
            yield nest.NestResponse(contents=json.dumps({'transcription': {'text': '테스트',
                'position': 0, 'startTimestamp': 0, 'endTimestamp': 200}}))
        server = grpc.server(ThreadPoolExecutor(max_workers=2))
        server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler(
            'com.nbp.cdncp.nest.grpc.proto.v1.NestService', {'recognize':
                grpc.stream_stream_rpc_method_handler(recognize,
                    request_deserializer=nest.NestRequest.FromString,
                    response_serializer=nest.NestResponse.SerializeToString)}),))
        port = server.add_insecure_port('127.0.0.1:0')
        server.start()
        channel = grpc.insecure_channel(f'127.0.0.1:{port}')
        try:
            with (patch('grpc.secure_channel', return_value=channel),
                 patch.dict('os.environ', {'CLOVA_SPEECH_SECRET': 'test-only'})):
                adapter = ClovaStreaming(StartRequest(provider='clova'))
                output = []
                adapter.run(iter([bytes(3200), bytes(3200)]), output.append)
                adapter.close()
            self.assertEqual([len(r.data.chunk) for r in received[1:]], [3200, 3200])
            self.assertTrue(json.loads(received[-1].data.extra_contents)['epFlag'])
            self.assertEqual(output[0].text, '테스트')
        finally:
            channel.close()
            server.stop(0).wait()

    def test_microphone_sends_every_block_and_drains(self):
        capture = MicrophoneBuffer()
        received, emitted, states, audio = [], [], [], []
        class Adapter:
            ready = threading.Event()
            def run(self, pcm, emit):
                self.ready.set()
                received.extend(pcm)
                emit(STTSegment(segment_id='seg_000001', text='끝', start_ms=0, end_ms=200, is_final=True))
            def close(self): pass
        class Stream:
            active = True
            def __init__(self, **kwargs): self.callback = kwargs['callback']
            def __enter__(self):
                self.callback(bytes(3200), 1600, None, False)
                self.callback(bytes(3200), 1600, None, False)
                capture.stop.set()
                return self
            def __exit__(self, *args): pass
        with (patch('app.stt.cloud_microphone.create_cloud_stream', return_value=Adapter()),
             patch('sounddevice.RawInputStream', Stream)):
            run_cloud_microphone(StartRequest(provider='google'), capture, states.append, emitted.append, audio.append)
        self.assertEqual(len(received), 2)
        self.assertEqual(sum(audio), 200)
        self.assertEqual(len(emitted), 1)
        self.assertEqual(states[-1], 'stopping')

    def test_live_cloud_does_not_load_whisper(self):
        session = LiveSession(StartRequest(provider='google'))
        with (patch('sounddevice.check_input_settings'),
             patch('app.stt.cloud_microphone.run_cloud_microphone') as cloud,
             patch('app.stt.runtime.runtime.create_service') as whisper):
            session.work()
        cloud.assert_called_once()
        whisper.assert_not_called()
        self.assertEqual(session.snapshot()['transport'], 'native-grpc')
        self.assertIsNone(session.snapshot()['summary']['rtf'])

    def test_errors_do_not_echo_credentials(self):
        self.assertNotIn('secret-value', safe_cloud_error(RuntimeError('secret-value')))
        with patch.dict('os.environ', {'CLOVA_SPEECH_SECRET': ''}):
            with self.assertRaises(CloudSetupError):
                ClovaStreaming(StartRequest(provider='clova'))


if __name__ == '__main__':
    unittest.main()
