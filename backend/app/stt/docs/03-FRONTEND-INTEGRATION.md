# 프론트엔드와 STT 통신

## 1 현재 구현과 앞으로 연결할 부분

`/docs`의 `/live/start`는 **서버 PC의 마이크**를 엽니다. 실제 서비스에서는 사용자 브라우저가 마이크 음성을 백엔드에 보내야 합니다.

| 경로 | 현재 지원 | 앞으로 할 일 |
|---|---|---|
| `/api/stt/live/*` | 서버 PC에서 Whisper·Google·CLOVA 비교 | 개발용으로 유지. 사용자 마이크 API로 사용하지 않음 |
| `WS /api/stt/stream` | 클라이언트 PCM → Whisper·Google·CLOVA 결과 | 프론트 캡처·리샘플링 연결 |
| 같은 WebSocket의 Google·CLOVA | **연결 구현, 모의 어댑터 자동 검증** | 실제 계정과 브라우저를 연결해 통합 검증 |
| 팀 `/api/v1/sessions/{session_id}/stream` | 명세상의 최종 경로 | 인증·실제 세션·문서·탐색 연결 |

`provider=faster-whisper/google/clova`로 제공자를 선택합니다. 생략하면 faster-whisper입니다. 인증키는 서버 환경변수에만 설정합니다.

```text
ws://localhost:5173/api/stt/stream?provider=faster-whisper&model=tiny&chunk_seconds=2
ws://localhost:5173/api/stt/stream?provider=google&language=ko&duration_seconds=240
ws://localhost:5173/api/stt/stream?provider=clova&language=ko&duration_seconds=300
```

model을 생략하면 제공자에 맞게 선택합니다. Google에 model=tiny를 붙이면 INVALID_CONFIG입니다. duration_seconds 기본값/최대값은 Google 240초, 나머지 1800초입니다. chunk_seconds는 Whisper만 사용합니다.

## 2 전체 흐름과 역할

```text
브라우저 마이크 → PCM 변환 → WebSocket → STT → stt.result
                                               ↓
                                    확정 문장 → 탐색 → highlight.update
```

| 담당 | 해야 할 일 |
|---|---|
| 프론트엔드 | 마이크 권한·캡처·리샘플링, WebSocket 송수신, 자막/PDF 하이라이트 표시 |
| STT | PCM 인식, 제공자 연결, STTSegment 반환 |
| API·통합 | 인증, 세션/문서 연결, 작업 자원 관리, STT→탐색 호출, 이벤트 전송 |
| 탐색 | 확정 문장으로 문서 위치 검색 |

## 3 로컬 프론트 연결 설정

백엔드는 02 문서대로 실행합니다. 서버 실행 전 같은 PowerShell에서:

```powershell
$env:STT_ALLOWED_ORIGINS = 'http://localhost:5173,http://127.0.0.1:5173'
```

실제 Vite 포트가 다르면 그 Origin을 명시합니다. 환경변수 변경 후 서버를 재시작합니다.
현재 WebSocket은 localhost 전용이며 이 설정만으로 원격 배포용 인증이 생기는 것은 아닙니다.

프론트 담당자가 `vite.config.ts`의 기존 `/api` 프록시에 `ws: true`를 추가합니다.

```ts
server: {
  proxy: {
    '/api': {
      target: 'http://127.0.0.1:8000',
      changeOrigin: true,
      ws: true,
    },
  },
}
```

백엔드가 8001이면 target도 8001로 바꿉니다. 브라우저는 자신의 Vite 주소를 통해 연결합니다.
changeOrigin은 Host를 바꾸지만 브라우저 Origin을 지우지 않으므로 위 허용 목록도 필요합니다.

## 4 실제 메시지 순서

연결 주소: `ws://localhost:5173/api/stt/stream?model=tiny&chunk_seconds=2` (Vite 프록시 기준).

1. 연결 후 클라이언트가 설정 JSON을 보냅니다.

```json
{"type":"session.start","payload":{"encoding":"pcm_s16le","sample_rate":16000,"channels":1}}
```

2. 서버 준비 응답을 받은 뒤 캡처/전송을 시작합니다.

```json
{"type":"session.ready","payload":{"session_id":"생성된-ID"}}
```

3. 클라이언트가 **바이너리 raw PCM**을 보내고 서버는 결과 JSON을 반복 반환합니다.

```json
{"type":"stt.result","payload":{"segment_id":"seg_000001","text":"안녕하세요.","start_ms":0,"end_ms":1800,"is_final":true}}
```

4. 종료 버튼을 누르면 캡처를 멈추고 남은 PCM까지 보낸 다음 종료 메시지를 보냅니다.

```json
{"type":"session.stop","payload":{}}
```

즉시 socket.close()를 호출하지 말고 마지막 결과와 서버의 정상 종료(code 1000)를 기다립니다.
오류 형식은 `{"type":"error","payload":{"code":"…","message":"…"}}`입니다.

## 5 프론트 담당자의 오디오 처리

- getUserMedia로 사용자 마이크 권한을 받습니다. 배포 환경은 HTTPS/WSS를 사용합니다.
- AudioWorklet 등으로 입력을 받고 실제 AudioContext.sampleRate를 확인합니다.
- 실제 44.1/48kHz 입력을 상태를 유지하는 리샘플러로 16kHz로 변환합니다. 이름만 16kHz로 바꾸면 안 됩니다.
- 다채널은 mono로 합치고 float32를 클리핑해 signed int16 little-endian으로 변환합니다.
- 100ms = 1600샘플 = 3200바이트씩 전송하는 것을 권장합니다. 무음도 보내야 오디오 시각이 유지됩니다.
- WebM/Opus 조각이나 WAV 헤더를 그대로 보내지 않습니다. 현재 WebSocket은 raw PCM만 받습니다.
- 입력 프레임 최대 32000바이트, 서버 대기 음성 최대 10초, 메시지 대기 15초, 테스트는 Google 최대 240초, 나머지 최대 30분입니다. 클라우드는 설정한 벽시계 시간이 되면 입력을 종료하고 최대 15초 동안 마지막 결과를 기다립니다. 클라이언트는 그 전에 캡처를 멈추고 session.stop을 보내는 것을 권장합니다.
- 중지·오류·컴포넌트 해제 시 마이크 트랙, AudioContext, 소켓을 정리합니다.

## 6 통신 코드 예시

아래는 **송수신 뼈대**입니다. 실제 마이크 캡처·리샘플링·화면 코드는 포함하지 않았습니다.

```ts
const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
const ws = new WebSocket(`${protocol}://${location.host}/api/stt/stream?model=tiny`);
let ready = false;
const segments = new Map();
const searched = new Set();

ws.onopen = () => ws.send(JSON.stringify({
  type: 'session.start',
  payload: { encoding: 'pcm_s16le', sample_rate: 16000, channels: 1 },
}));

ws.onmessage = ({ data }) => {
  const event = JSON.parse(data);
  if (event.type === 'session.ready') {
    ready = true; // 이때 마이크 캡처 시작
  } else if (event.type === 'stt.result') {
    const segment = event.payload;
    segments.set(segment.segment_id, segment); // 같은 ID는 교체
    if (segment.is_final && !searched.has(segment.segment_id)) {
      searched.add(segment.segment_id);
      // 팀의 확정 문장 탐색 흐름에 전달
    }
    // Map의 현재 값을 UI에 반영
  } else if (event.type === 'error') {
    ready = false;
    // 오류 표시 + 마이크/AudioContext 정리
  }
};
ws.onclose = ws.onerror = () => {
  ready = false;
  // 마이크 트랙/AudioContext 정리, 종료 또는 연결 오류 표시
};

// 이미 16kHz mono로 변환된 int16 샘플만 전달
function sendPcm16(samples: Int16Array) {
  if (!ready || ws.readyState !== WebSocket.OPEN) return;
  if (samples.length === 0 || samples.length > 16000) throw new Error('잘못된 프레임 크기');
  if (ws.bufferedAmount > 320000) throw new Error('전송 지연: 캡처 중단 후 재시작 필요');
  const buffer = new ArrayBuffer(samples.length * 2);
  const view = new DataView(buffer);
  samples.forEach((sample, i) => view.setInt16(i * 2, sample, true));
  ws.send(buffer);
}

function finishAfterFlushingAudio() {
  if (ready && ws.readyState === WebSocket.OPEN) {
    ready = false;
    ws.send(JSON.stringify({ type: 'session.stop', payload: {} }));
  }
}
```

실제 구현은 예외를 잡아 캡처를 정리해야 합니다. 새 세션에서는 Map/Set도 초기화합니다.
Whisper·CLOVA는 확정 결과를 반환합니다. Google은 중간 결과와 확정 결과에 같은 ID를 사용하므로 기존 문장을 교체하고, 확정 결과만 탐색에 전달합니다.

파일 업로드만 연결할 때는 FormData에 `file`, `provider`, `model`을 넣어 POST `/api/stt/transcribe`로 전송합니다. 브라우저가 multipart 경계를 만들도록 Content-Type을 직접 지정하지 않습니다. 이 경로는 실시간 캡처 스트림이 아닙니다.

## 7 클라우드 연결 구현과 남은 통합 작업

`stt_stream.py`의 `serve_cloud_socket`이 `CloudSocketBridge`를 통해 브라우저 PCM을 기존 Google/CLOVA gRPC 어댑터로 보냅니다. 입력은 최대 10초·128개 메시지, 결과 대기열은 최대 128개로 제한합니다. 연결 해제·오류 시 작업을 취소하고 gRPC 채널을 닫습니다. session.stop에서는 이미 받은 음성을 처리한 뒤 최종 결과를 보내고 code 1000으로 종료합니다.

`INVALID_CONFIG`, `INVALID_FORMAT`, `INVALID_AUDIO`, `INVALID_MESSAGE`, `BACKPRESSURE`, `SESSION_LIMIT`, `TIMEOUT`, `STT_BUSY`, `CLOUD_FAILED` 오류를 화면에 표시하세요. CLOUD_FAILED는 코드 1011, 입력/제한 오류는 1008로 닫습니다. session.ready는 오디오 전송 준비 신호이며 Google 계정 인증 성공을 보장하지 않습니다. 이후 error도 처리해야 합니다.

프론트 마이크 캡처, 실제 클라우드 계정 통합 테스트, Google 제한 이후 새 세션/재연결 정책은 여전히 필요합니다. 새 세션은 segment_id와 오디오 시각이 다시 시작되므로 session_id로 구분하세요. `/docs`는 WebSocket을 직접 실행하지 않으며 기존 PC 마이크 테스트는 그대로 사용할 수 있습니다.

팀의 최종 세션 라우터에서는 인증과 실제 session_id 확인 후 모델을 선택하고, 확정 결과만 탐색으로 전달합니다.
현재 테스트용 UUID는 팀 문서 세션을 생성한 것이 아닙니다. 실습용 main.py를 덮어쓰지 말고 필요한 라우터/서비스만 통합하세요.
단일 프로세스 작업 잠금, 다중 사용자 큐, 재연결 복구, 인증과 배포는 별도 설계가 필요합니다.

참고: [AudioWorklet](https://developer.mozilla.org/en-US/docs/Web/API/AudioWorklet), [Vite 프록시](https://vite.dev/config/server-options#server-proxy), [FastAPI WebSocket](https://fastapi.tiangolo.com/advanced/websockets/).
