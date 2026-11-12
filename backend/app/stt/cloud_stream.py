"""Native Google v1 and CLOVA NEST bidirectional gRPC (not REST chunk requests)."""
import json
import os
import threading

from app.schemas.stt_models import STTSegment


class CloudSetupError(RuntimeError):
    pass


def safe_cloud_error(error):
    if isinstance(error, CloudSetupError):
        return str(error)
    import grpc
    if isinstance(error, grpc.RpcError):
        code = error.code().name
    else:
        code = type(error).__name__
    # Never include SDK details: they may contain credential paths/request metadata.
    return f"클라우드 스트리밍 실패 ({code}). 인증·API 권한·할당량·연결을 확인하세요."


def millis(duration):
    if hasattr(duration, 'total_seconds'):
        return round(duration.total_seconds() * 1000)
    return round(duration.seconds * 1000 + duration.nanos / 1000000)


class GoogleResults:
    def __init__(self):
        self.counter = 1
        self.final_end = 0

    def parse(self, response):
        out, partials = [], []
        partial_end = self.final_end
        for result in response.results:
            if not result.alternatives:
                continue
            best = result.alternatives[0]
            text = best.transcript.strip()
            end = millis(result.result_end_time)
            if not text or end <= self.final_end:
                continue
            if not result.is_final:
                partials.append(text)
                partial_end = max(partial_end, end)
                continue
            start = max(self.final_end, millis(best.words[0].start_time)) if best.words else self.final_end
            out.append(STTSegment(segment_id=f"seg_{self.counter:06d}", text=text,
                       start_ms=min(start, end), end_ms=end, is_final=True))
            self.final_end = end
            self.counter += 1
        if partials and partial_end > self.final_end:
            out.append(STTSegment(segment_id=f"seg_{self.counter:06d}", text=' '.join(partials),
                       start_ms=self.final_end, end_ms=partial_end, is_final=False))
        return out


class GoogleStreaming:
    def __init__(self, settings):
        from google.cloud import speech_v1 as speech
        import google.auth
        from google.oauth2.credentials import Credentials
        self.speech = speech
        self.settings = settings
        self.call = None
        self.ready = threading.Event()
        try:
            token = os.environ.get('GOOGLE_ACCESS_TOKEN', '').strip()
            project = os.environ.get('GOOGLE_CLOUD_PROJECT') or None
            if token:
                credentials = Credentials(token=token, quota_project_id=project)
            else:
                credentials, _ = google.auth.default(
                    scopes=['https://www.googleapis.com/auth/cloud-platform'], quota_project_id=project)
            self.client = speech.SpeechClient(credentials=credentials)
        except Exception:
            raise CloudSetupError("Google 인증을 준비하세요: gcloud auth application-default login "
                                  "또는 GOOGLE_APPLICATION_CREDENTIALS 설정 후 서버 재시작.") from None

    def run(self, pcm, emit):
        speech = self.speech
        config = speech.StreamingRecognitionConfig(
            config=speech.RecognitionConfig(encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=16000, audio_channel_count=1,
                language_code={'ko': 'ko-KR', 'en': 'en-US', 'ja': 'ja-JP'}[self.settings.language],
                model='default', enable_automatic_punctuation=True, enable_word_time_offsets=True),
            interim_results=True, single_utterance=False)
        # Google has no config-ack event; audio is needed before first recognition result.
        self.ready.set()
        self.call = self.client.streaming_recognize(config=config,
            requests=(speech.StreamingRecognizeRequest(audio_content=data) for data in pcm),
            retry=None, timeout=self.settings.duration_seconds + 30)
        mapper = GoogleResults()
        for response in self.call:
            if response.error.code:
                raise CloudSetupError(f"Google 스트리밍 응답 오류(code={response.error.code}).")
            for segment in mapper.parse(response):
                emit(segment)

    def close(self):
        if self.call is not None and hasattr(self.call, 'cancel'):
            self.call.cancel()
        self.client.transport.close()


class ClovaResults:
    def __init__(self):
        self.counter = 0
        self.last_position = -1

    def parse(self, data):
        # Configuration and recognition failure statuses must not be mistaken for silence.
        for key in ('config', 'recognize'):
            section = data.get(key, {})
            if isinstance(section, dict):
                statuses = [section.get('status')] + [v.get('status') for v in section.values() if isinstance(v, dict)]
                if any(s is not None and s != 'Success' for s in statuses):
                    raise CloudSetupError("CLOVA 설정/인식 요청이 거부됐습니다. Basic 도메인, Secret Key, 언어 권한과 슬롯을 확인하세요.")
        item = data.get('transcription')
        if not item or not item.get('text', '').strip():
            return []
        start, end = item.get('startTimestamp'), item.get('endTimestamp')
        position = item.get('position')
        if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end < start or not isinstance(position, int):
            raise CloudSetupError("CLOVA 결과의 시각/position 형식이 올바르지 않습니다.")
        if position <= self.last_position:
            return []
        self.last_position = position
        self.counter += 1
        return [STTSegment(segment_id=f'seg_{self.counter:06d}', text=item['text'].strip(),
                           start_ms=start, end_ms=end, is_final=True)]


class ClovaStreaming:
    def __init__(self, settings):
        import grpc
        from .proto import nest_pb2 as nest
        self.nest, self.settings = nest, settings
        self.secret = os.environ.get('CLOVA_SPEECH_SECRET', '').strip()
        if not self.secret:
            raise CloudSetupError("CLOVA Basic 도메인의 Secret Key를 CLOVA_SPEECH_SECRET에 설정하고 서버를 재시작하세요.")
        self.channel = grpc.secure_channel('clovaspeech-gw.ncloud.com:50051', grpc.ssl_channel_credentials())
        self.rpc = self.channel.stream_stream('/com.nbp.cdncp.nest.grpc.proto.v1.NestService/recognize',
            request_serializer=nest.NestRequest.SerializeToString,
            response_deserializer=nest.NestResponse.FromString)
        self.ready = threading.Event()
        self.cancelled = threading.Event()
        self.call = None

    def requests(self, pcm):
        n = self.nest
        yield n.NestRequest(type=n.CONFIG, config=n.NestConfig(config=json.dumps({
            'transcription': {'language': self.settings.language},
            'semanticEpd': {'skipEmptyText': True, 'useWordEpd': True, 'usePeriodEpd': True,
                            'gapThreshold': 500, 'durationThreshold': 2000}})))
        # Wait for CLOVA's Success config response before sending any audio.
        for _ in range(150):
            if self.ready.wait(.1):
                break
            if self.cancelled.is_set():
                return
        else:
            raise CloudSetupError("CLOVA 설정 응답 대기 시간이 초과되었습니다.")
        sequence = 0
        pending = None
        for data in pcm:
            if pending is not None:
                sequence += 1
                yield n.NestRequest(type=n.DATA, data=n.NestData(chunk=pending,
                    extra_contents=json.dumps({'seqId': sequence, 'epFlag': False})))
            pending = data
        # Mark the final actual audio frame; avoid sending a zero-length PCM request.
        if pending is not None and not self.cancelled.is_set():
            sequence += 1
            yield n.NestRequest(type=n.DATA, data=n.NestData(chunk=pending,
                extra_contents=json.dumps({'seqId': sequence, 'epFlag': True})))

    def run(self, pcm, emit):
        self.call = self.rpc(self.requests(pcm), metadata=(('authorization', f'Bearer {self.secret}'),),
                             timeout=self.settings.duration_seconds + 30)
        mapper = ClovaResults()
        for response in self.call:
            data = json.loads(response.contents)
            segments = mapper.parse(data)
            if data.get('config', {}).get('status') == 'Success':
                self.ready.set()
            for segment in segments:
                emit(segment)

    def close(self):
        self.cancelled.set()
        if self.call is not None:
            self.call.cancel()
        self.channel.close()


def create_cloud_stream(settings):
    return {'google': GoogleStreaming, 'clova': ClovaStreaming}[settings.provider](settings)
